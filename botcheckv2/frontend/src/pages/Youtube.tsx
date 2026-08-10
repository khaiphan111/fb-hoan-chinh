import { IconDeviceFloppy, IconPlus, IconTrash, IconEdit } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Button, Card, CardContent, CardHeader, CardTitle, Input, Label } from "../components/ui";
import { api } from "../lib/api";

export default function Youtube() {
  const [tracks, setTracks] = useState<any[]>([]);
  const [videoTracks, setVideoTracks] = useState<any[]>([]);
  const [username, setUsername] = useState("");
  const [videoUrl, setVideoUrl] = useState("");
  const [interval, setInterval] = useState("60");
  const [editVideoId, setEditVideoId] = useState<string | null>(null);
  const [editInterval, setEditInterval] = useState("60");

  async function load() {
    try {
      const t = await api("/api/admin/yt-tracks");
      setTracks(t);
      const v = await api("/api/admin/yt-video-tracks");
      setVideoTracks(v);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function addTrack() {
    if (!username) return;
    try {
      await api("/api/admin/yt-tracks", { method: "POST", body: JSON.stringify({ yt_username: username }) });
      toast.success("Thêm thành công!");
      setUsername("");
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function addVideo() {
    if (!videoUrl) return;
    try {
      await api("/api/admin/yt-video-tracks", { 
        method: "POST", 
        body: JSON.stringify({ video_url: videoUrl, check_interval: Number(interval) * 60 }) 
      });
      toast.success("Thêm thành công!");
      setVideoUrl("");
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function delTrack(yt_username: string) {
    if (!confirm("Xóa theo dõi tài khoản này?")) return;
    try {
      await api(`/api/admin/yt-tracks/${yt_username}`, { method: "DELETE" });
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function delVideo(video_id: string) {
    if (!confirm("Xóa theo dõi video này?")) return;
    try {
      await api(`/api/admin/yt-video-tracks/${video_id}`, { method: "DELETE" });
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function updateVideoInterval(video_id: string) {
    try {
      await api(`/api/admin/yt-video-tracks/${video_id}`, { 
        method: "PUT", 
        body: JSON.stringify({ check_interval: Number(editInterval) * 60 }) 
      });
      toast.success("Sửa thành công!");
      setEditVideoId(null);
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  return (
    <div className="flex flex-col gap-6 max-w-4xl">
      <h1 className="text-2xl font-semibold">Youtube Tracking</h1>

      <Card>
        <CardHeader>
          <CardTitle>Tài khoản (Follower)</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex gap-2">
            <Input 
              value={username} 
              onChange={e => setUsername(e.target.value)} 
              placeholder="Username TikTok" 
            />
            <Button onClick={addTrack}><IconPlus size={18} /> Thêm</Button>
          </div>
          
          <div className="rounded-md border border-border">
            <table className="w-full text-sm text-left">
              <thead className="bg-muted border-b border-border">
                <tr>
                  <th className="px-4 py-2 font-medium">Username</th>
                  <th className="px-4 py-2 font-medium">Subscribers</th>
                  <th className="px-4 py-2 font-medium">Following</th>
                  <th className="px-4 py-2 font-medium">Videos</th>
                  <th className="px-4 py-2 font-medium">Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {tracks.map(t => (
                  <tr key={t.yt_username} className="border-b border-border last:border-0 hover:bg-muted/50">
                    <td className="px-4 py-2 font-semibold flex items-center gap-3">
                      {t.avatar_url ? (
                        <img src={t.avatar_url} className="w-10 h-10 rounded-full bg-muted object-cover border border-border" alt="" onError={e => (e.target as HTMLImageElement).style.display = 'none'} />
                      ) : null}
                      @{t.yt_username}
                    </td>
                    <td className="px-4 py-2">{t.last_subscribers?.toLocaleString()}</td>
                    <td className="px-4 py-2">{t.last_following?.toLocaleString()}</td>
                    <td className="px-4 py-2">{t.last_videos?.toLocaleString()}</td>
                    <td className="px-4 py-2">
                      <Button variant="ghost" size="sm" onClick={() => delTrack(t.yt_username)} className="text-die hover:text-die/80 h-8 px-2">
                        <IconTrash size={16} />
                      </Button>
                    </td>
                  </tr>
                ))}
                {tracks.length === 0 && (
                  <tr><td colSpan={5} className="px-4 py-4 text-center text-muted-foreground">Chưa có dữ liệu</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Videos (Tương tác)</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex gap-2 items-end">
            <div className="flex-1 flex flex-col gap-1.5">
              <Label>Link Video TikTok</Label>
              <Input 
                value={videoUrl} 
                onChange={e => setVideoUrl(e.target.value)} 
                placeholder="https://www.tiktok.com/@user/video/123" 
              />
            </div>
            <div className="w-32 flex flex-col gap-1.5">
              <Label>Chu kỳ (Phút)</Label>
              <Input 
                type="number" 
                value={interval} 
                onChange={e => setInterval(e.target.value)} 
              />
            </div>
            <Button onClick={addVideo} className="mb-0.5"><IconPlus size={18} /> Thêm</Button>
          </div>
          
          <div className="rounded-md border border-border">
            <table className="w-full text-sm text-left">
              <thead className="bg-muted border-b border-border">
                <tr>
                  <th className="px-4 py-2 font-medium">Video</th>
                  <th className="px-4 py-2 font-medium">Tương tác</th>
                  <th className="px-4 py-2 font-medium">Chu kỳ</th>
                  <th className="px-4 py-2 font-medium">Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {videoTracks.map(t => (
                  <tr key={t.video_id} className="border-b border-border last:border-0 hover:bg-muted/50">
                    <td className="px-4 py-2 max-w-[250px]">
                      <div className="flex gap-3 items-center">
                        {t.cover_url ? (
                          <img src={t.cover_url} className="w-12 h-16 rounded-md bg-muted object-cover shrink-0 border border-border" alt="" onError={e => (e.target as HTMLImageElement).style.display = 'none'} />
                        ) : null}
                        <div className="truncate flex flex-col gap-1">
                          <a href={t.video_url} target="_blank" className="text-blue-500 hover:underline font-medium truncate">
                            @{t.yt_username}
                          </a>
                          <span className="text-xs text-muted-foreground truncate" title={t.video_desc}>{t.video_desc || "Không có mô tả"}</span>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex gap-3 text-xs">
                        <span>▶️ {t.last_views?.toLocaleString()}</span>
                        <span>❤️ {t.last_likes?.toLocaleString()}</span>
                        <span>💬 {t.last_comments?.toLocaleString()}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2">
                      {editVideoId === t.video_id ? (
                        <div className="flex items-center gap-1">
                          <Input type="number" value={editInterval} onChange={e => setEditInterval(e.target.value)} className="w-16 h-8 px-2" />
                          <Button size="sm" variant="outline" className="h-8 px-2 border-green-500 text-green-500 hover:bg-green-500/10" onClick={() => updateVideoInterval(t.video_id)}>
                            <IconDeviceFloppy size={16} />
                          </Button>
                          <Button size="sm" variant="ghost" className="h-8 px-2 text-muted-foreground" onClick={() => setEditVideoId(null)}>Hủy</Button>
                        </div>
                      ) : (
                        <div className="flex items-center gap-2">
                          {t.check_interval / 60}p
                          <Button variant="ghost" size="sm" onClick={() => { setEditVideoId(t.video_id); setEditInterval(String(t.check_interval / 60)); }} className="h-6 px-1.5 text-muted-foreground">
                            <IconEdit size={14} />
                          </Button>
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-2">
                      <Button variant="ghost" size="sm" onClick={() => delVideo(t.video_id)} className="text-die hover:text-die/80 h-8 px-2">
                        <IconTrash size={16} />
                      </Button>
                    </td>
                  </tr>
                ))}
                {videoTracks.length === 0 && (
                  <tr><td colSpan={4} className="px-4 py-4 text-center text-muted-foreground">Chưa có dữ liệu</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

    </div>
  );
}
