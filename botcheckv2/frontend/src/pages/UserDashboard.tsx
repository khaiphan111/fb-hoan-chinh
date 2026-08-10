import { IconListCheck, IconLogout, IconUser, IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { api } from "../lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui";

export default function UserDashboard({ onLogout }: { onLogout: () => void }) {
  const [user, setUser] = useState<any>(null);
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    try {
      const [u, d] = await Promise.all([
        api("/api/user/me"),
        api("/api/user/analytics")
      ]);
      setUser(u);
      setData(d);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function handleDelete(type: string, target: string) {
    if (!confirm("Bạn có chắc chắn muốn xóa mục này?")) return;
    try {
      await api(`/api/user/tracks/${type}/${encodeURIComponent(target)}`, { method: "DELETE" });
      toast.success("Xóa thành công!");
      loadData();
    } catch (e: any) {
      toast.error(e.message || "Có lỗi xảy ra");
    }
  }

  return (
    <div className="min-h-screen bg-background flex flex-col">
      <header className="border-b border-border p-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <IconUser size={24} />
          <h1 className="text-xl font-bold">Xin chào, {user?.name || "Khách hàng"}</h1>
        </div>
        <div className="flex items-center gap-4">
          <div className="text-sm">
            <span className="text-muted-foreground">Số dư: </span>
            <span className="font-semibold text-green-500">
              {new Intl.NumberFormat("vi-VN").format(user?.balance || 0)}đ
            </span>
          </div>
          <div className="text-sm">
            <span className="text-muted-foreground">VIP: </span>
            <span className="font-semibold text-yellow-500">
              Cấp {user?.vip_level || 0}
            </span>
          </div>
          <button
            onClick={onLogout}
            className="flex items-center gap-1 text-red-400 hover:text-red-500 text-sm font-medium"
          >
            <IconLogout size={16} /> Thoát
          </button>
        </div>
      </header>

      <main className="p-6 flex-1 flex flex-col gap-6 max-w-6xl mx-auto w-full">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground font-normal">Đang theo dõi FB Live/Die</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{data?.live || 0 + data?.die || 0}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground font-normal">FB Posts đang theo dõi</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{data?.fb_tracks?.length || 0}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground font-normal">TK/IG/YT đang theo dõi</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{(data?.tk_tracks?.length || 0) + (data?.ig_tracks?.length || 0) + (data?.yt_tracks?.length || 0)}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground font-normal">TK/IG/YT Video đang theo dõi</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{(data?.tk_videos?.length || 0) + (data?.ig_videos?.length || 0) + (data?.yt_videos?.length || 0)}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground font-normal">Zalo đang theo dõi</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{data?.zalo_tracks?.length || 0}</div>
            </CardContent>
          </Card>

        </div>

        <Card className="flex-1">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <IconListCheck size={20} /> Danh sách TikTok đang theo dõi
            </CardTitle>
          </CardHeader>
          <CardContent>
            {data?.tk_tracks?.length > 0 ? (
              <div className="border border-border rounded overflow-hidden">
                <table className="w-full text-left text-sm">
                  <thead className="bg-muted">
                    <tr>
                      <th className="p-2 border-b border-border">Username</th>
                      <th className="p-2 border-b border-border">Followers</th>
                      <th className="p-2 border-b border-border">Videos</th>
                      <th className="p-2 border-b border-border">Trạng thái</th>
                      <th className="p-2 border-b border-border text-right">Hành động</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.tk_tracks.map((t: any) => (
                      <tr key={t.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                        <td className="p-2 font-medium">@{t.tiktok_username}</td>
                        <td className="p-2">{new Intl.NumberFormat().format(t.last_followers || 0)}</td>
                        <td className="p-2">{new Intl.NumberFormat().format(t.last_videos || 0)}</td>
                        <td className="p-2">
                          <span className={`px-2 py-0.5 rounded text-xs ${t.active ? 'bg-green-500/20 text-green-500' : 'bg-red-500/20 text-red-500'}`}>
                            {t.active ? "Đang chạy" : "Tạm dừng"}
                          </span>
                        </td>
                        <td className="p-2 text-right">
                          <button onClick={() => handleDelete('tk_track', t.tiktok_username)} className="text-red-500 hover:text-red-700 p-1 bg-red-500/10 rounded">
                            <IconTrash size={16} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-center py-6 text-muted-foreground">
                Bạn chưa theo dõi tài khoản TikTok nào.
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="flex-1">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <IconListCheck size={20} /> Danh sách Video TikTok
            </CardTitle>
          </CardHeader>
          <CardContent>
            {data?.tk_videos?.length > 0 ? (
              <div className="border border-border rounded overflow-hidden">
                <table className="w-full text-left text-sm">
                  <thead className="bg-muted">
                    <tr>
                      <th className="p-2 border-b border-border">Username</th>
                      <th className="p-2 border-b border-border">Video ID</th>
                      <th className="p-2 border-b border-border">Lượt xem</th>
                      <th className="p-2 border-b border-border">Likes</th>
                      <th className="p-2 border-b border-border">Trạng thái</th>
                      <th className="p-2 border-b border-border text-right">Hành động</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.tk_videos.map((t: any) => (
                      <tr key={t.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                        <td className="p-2 font-medium">@{t.tiktok_username || "N/A"}</td>
                        <td className="p-2"><a href={t.video_url} target="_blank" rel="noreferrer" className="text-blue-500 hover:underline">{t.video_id}</a></td>
                        <td className="p-2">{new Intl.NumberFormat().format(t.last_plays || 0)}</td>
                        <td className="p-2">{new Intl.NumberFormat().format(t.last_likes || 0)}</td>
                        <td className="p-2">
                          <span className={`px-2 py-0.5 rounded text-xs ${t.active ? 'bg-green-500/20 text-green-500' : 'bg-red-500/20 text-red-500'}`}>
                            {t.active ? "Đang chạy" : "Tạm dừng"}
                          </span>
                        </td>
                        <td className="p-2 text-right">
                          <button onClick={() => handleDelete('tk_video', t.video_id)} className="text-red-500 hover:text-red-700 p-1 bg-red-500/10 rounded">
                            <IconTrash size={16} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-center py-6 text-muted-foreground">
                Bạn chưa theo dõi Video TikTok nào.
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="flex-1">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <IconListCheck size={20} /> Danh sách Instagram
            </CardTitle>
          </CardHeader>
          <CardContent>
            {data?.ig_tracks?.length > 0 ? (
              <div className="border border-border rounded overflow-hidden">
                <table className="w-full text-left text-sm">
                  <thead className="bg-muted">
                    <tr>
                      <th className="p-2 border-b border-border">Username</th>
                      <th className="p-2 border-b border-border">Followers</th>
                      <th className="p-2 border-b border-border">Posts</th>
                      <th className="p-2 border-b border-border">Trạng thái</th>
                      <th className="p-2 border-b border-border text-right">Hành động</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.ig_tracks.map((t: any) => (
                      <tr key={t.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                        <td className="p-2 font-medium">@{t.ig_username}</td>
                        <td className="p-2">{new Intl.NumberFormat().format(t.last_followers || 0)}</td>
                        <td className="p-2">{new Intl.NumberFormat().format(t.last_posts || 0)}</td>
                        <td className="p-2">
                          <span className={`px-2 py-0.5 rounded text-xs ${t.active ? 'bg-green-500/20 text-green-500' : 'bg-red-500/20 text-red-500'}`}>
                            {t.active ? "Đang chạy" : "Tạm dừng"}
                          </span>
                        </td>
                        <td className="p-2 text-right">
                          <button onClick={() => handleDelete('ig_track', t.ig_username)} className="text-red-500 hover:text-red-700 p-1 bg-red-500/10 rounded">
                            <IconTrash size={16} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-center py-6 text-muted-foreground">
                Bạn chưa theo dõi tài khoản Instagram nào.
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="flex-1">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <IconListCheck size={20} /> Danh sách Bài viết / Video Instagram
            </CardTitle>
          </CardHeader>
          <CardContent>
            {data?.ig_videos?.length > 0 ? (
              <div className="border border-border rounded overflow-hidden">
                <table className="w-full text-left text-sm">
                  <thead className="bg-muted">
                    <tr>
                      <th className="p-2 border-b border-border">Username</th>
                      <th className="p-2 border-b border-border">Post ID</th>
                      <th className="p-2 border-b border-border">Views</th>
                      <th className="p-2 border-b border-border">Likes</th>
                      <th className="p-2 border-b border-border">Trạng thái</th>
                      <th className="p-2 border-b border-border text-right">Hành động</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.ig_videos.map((t: any) => (
                      <tr key={t.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                        <td className="p-2 font-medium">@{t.ig_username || "N/A"}</td>
                        <td className="p-2"><a href={t.post_url} target="_blank" rel="noreferrer" className="text-blue-500 hover:underline">{t.post_id}</a></td>
                        <td className="p-2">{new Intl.NumberFormat().format(t.last_views || 0)}</td>
                        <td className="p-2">{new Intl.NumberFormat().format(t.last_likes || 0)}</td>
                        <td className="p-2">
                          <span className={`px-2 py-0.5 rounded text-xs ${t.active ? 'bg-green-500/20 text-green-500' : 'bg-red-500/20 text-red-500'}`}>
                            {t.active ? "Đang chạy" : "Tạm dừng"}
                          </span>
                        </td>
                        <td className="p-2 text-right">
                          <button onClick={() => handleDelete('ig_video', t.post_id)} className="text-red-500 hover:text-red-700 p-1 bg-red-500/10 rounded">
                            <IconTrash size={16} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-center py-6 text-muted-foreground">
                Bạn chưa theo dõi Bài viết / Video Instagram nào.
              </div>
            )}
          </CardContent>
        </Card>
        
        <Card className="flex-1">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <IconListCheck size={20} /> Danh sách FB Live/Die
            </CardTitle>
          </CardHeader>
          <CardContent>
            {data?.fb_watches?.length > 0 ? (
              <div className="border border-border rounded overflow-hidden">
                <table className="w-full text-left text-sm">
                  <thead className="bg-muted">
                    <tr>
                      <th className="p-2 border-b border-border">UID</th>
                      <th className="p-2 border-b border-border">Ghi chú</th>
                      <th className="p-2 border-b border-border">Trạng thái</th>
                      <th className="p-2 border-b border-border">Hoạt động</th>
                      <th className="p-2 border-b border-border text-right">Hành động</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.fb_watches.map((t: any) => (
                      <tr key={t.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                        <td className="p-2 font-medium">{t.uid}</td>
                        <td className="p-2">{t.note}</td>
                        <td className="p-2">
                          {t.last_status === 'live' ? <span className="text-green-500 font-bold">Live</span> : (t.last_status === 'die' ? <span className="text-red-500 font-bold">Die</span> : t.last_status)}
                        </td>
                        <td className="p-2">
                          <span className={`px-2 py-0.5 rounded text-xs ${t.active ? 'bg-green-500/20 text-green-500' : 'bg-red-500/20 text-red-500'}`}>
                            {t.active ? "Đang chạy" : "Tạm dừng"}
                          </span>
                        </td>
                        <td className="p-2 text-right">
                          <button onClick={() => handleDelete('fb_watch', t.uid)} className="text-red-500 hover:text-red-700 p-1 bg-red-500/10 rounded">
                            <IconTrash size={16} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-center py-6 text-muted-foreground">
                Bạn chưa theo dõi FB Live/Die nào.
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="flex-1">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <IconListCheck size={20} /> Danh sách Bài viết FB
            </CardTitle>
          </CardHeader>
          <CardContent>
            {data?.fb_tracks?.length > 0 ? (
              <div className="border border-border rounded overflow-hidden">
                <table className="w-full text-left text-sm">
                  <thead className="bg-muted">
                    <tr>
                      <th className="p-2 border-b border-border">Post ID</th>
                      <th className="p-2 border-b border-border">Likes</th>
                      <th className="p-2 border-b border-border">Comments</th>
                      <th className="p-2 border-b border-border">Shares</th>
                      <th className="p-2 border-b border-border">Trạng thái</th>
                      <th className="p-2 border-b border-border text-right">Hành động</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.fb_tracks.map((t: any) => (
                      <tr key={t.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                        <td className="p-2 font-medium"><a href={t.post_url} target="_blank" rel="noreferrer" className="text-blue-500 hover:underline">{t.post_id}</a></td>
                        <td className="p-2">{new Intl.NumberFormat().format(t.last_likes || 0)}</td>
                        <td className="p-2">{new Intl.NumberFormat().format(t.last_comments || 0)}</td>
                        <td className="p-2">{new Intl.NumberFormat().format(t.last_shares || 0)}</td>
                        <td className="p-2">
                          <span className={`px-2 py-0.5 rounded text-xs ${t.active ? 'bg-green-500/20 text-green-500' : 'bg-red-500/20 text-red-500'}`}>
                            {t.active ? "Đang chạy" : "Tạm dừng"}
                          </span>
                        </td>
                        <td className="p-2 text-right">
                          <button onClick={() => handleDelete('fb_track', t.post_id)} className="text-red-500 hover:text-red-700 p-1 bg-red-500/10 rounded">
                            <IconTrash size={16} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-center py-6 text-muted-foreground">
                Bạn chưa theo dõi Bài viết FB nào.
              </div>
            )}
          </CardContent>
        </Card>
      
        <Card className="flex-1 mt-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <IconListCheck size={20} /> Danh sách YouTube đang theo dõi
            </CardTitle>
          </CardHeader>
          <CardContent>
            {data?.yt_tracks?.length > 0 ? (
              <div className="border border-border rounded overflow-hidden">
                <table className="w-full text-left text-sm">
                  <thead className="bg-muted">
                    <tr>
                      <th className="p-2 border-b border-border">Kênh</th>
                      <th className="p-2 border-b border-border">Đăng ký</th>
                      <th className="p-2 border-b border-border">Video</th>
                      <th className="p-2 border-b border-border">Trạng thái</th>
                      <th className="p-2 border-b border-border text-right">Hành động</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.yt_tracks.map((t: any) => (
                      <tr key={t.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                        <td className="p-2 font-medium">{t.yt_username}</td>
                        <td className="p-2">{new Intl.NumberFormat().format(t.last_subscribers || 0)}</td>
                        <td className="p-2">{new Intl.NumberFormat().format(t.last_videos || 0)}</td>
                        <td className="p-2">
                          <span className={`px-2 py-0.5 rounded text-xs ${t.active ? 'bg-green-500/20 text-green-500' : 'bg-red-500/20 text-red-500'}`}>
                            {t.active ? "Đang chạy" : "Tạm dừng"}
                          </span>
                        </td>
                        <td className="p-2 text-right">
                          <button onClick={() => handleDelete('yt', t.id)} className="text-red-500 hover:text-red-400 p-1 rounded hover:bg-red-500/10">
                            <IconTrash size={16} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-center text-muted-foreground p-4">Chưa có mục nào</div>
            )}
          </CardContent>
        </Card>

        <Card className="flex-1 mt-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <IconListCheck size={20} /> Danh sách Video YouTube
            </CardTitle>
          </CardHeader>
          <CardContent>
            {data?.yt_videos?.length > 0 ? (
              <div className="border border-border rounded overflow-hidden overflow-x-auto">
                <table className="w-full text-left text-sm whitespace-nowrap">
                  <thead className="bg-muted">
                    <tr>
                      <th className="p-2 border-b border-border">Video ID</th>
                      <th className="p-2 border-b border-border">Kênh</th>
                      <th className="p-2 border-b border-border">Lượt xem</th>
                      <th className="p-2 border-b border-border">Lượt thích</th>
                      <th className="p-2 border-b border-border text-right">Hành động</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.yt_videos.map((t: any) => (
                      <tr key={t.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                        <td className="p-2 font-medium max-w-[150px] truncate" title={t.video_desc || t.video_id}>
                          <a href={t.video_url} target="_blank" rel="noreferrer" className="text-blue-500 hover:underline">
                            {t.video_id}
                          </a>
                        </td>
                        <td className="p-2">{t.yt_username}</td>
                        <td className="p-2">{new Intl.NumberFormat().format(t.last_views || 0)}</td>
                        <td className="p-2">{new Intl.NumberFormat().format(t.last_likes || 0)}</td>
                        <td className="p-2 text-right">
                          <button onClick={() => handleDelete('yt-video', t.id)} className="text-red-500 hover:text-red-400 p-1 rounded hover:bg-red-500/10">
                            <IconTrash size={16} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-center text-muted-foreground p-4">Chưa có mục nào</div>
            )}
          </CardContent>
        </Card>

      
        {data?.zalo_tracks?.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Danh sách SĐT Zalo đang theo dõi</CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead className="bg-muted text-muted-foreground">
                  <tr>
                    <th className="p-3">SĐT</th>
                    <th className="p-3">Tên Zalo</th>
                    <th className="p-3">Trạng thái</th>
                    <th className="p-3">Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {data.zalo_tracks.map((t: any) => (
                    <tr key={t.id} className="border-b">
                      <td className="p-3">{t.phone}</td>
                      <td className="p-3 font-semibold">{t.name || "-"}</td>
                      <td className="p-3">
                        <span className={`px-2 py-1 rounded text-xs font-semibold ${t.status === "LIVE" ? "bg-live/20 text-live" : "bg-die/20 text-die"}`}>
                          {t.status}
                        </span>
                      </td>
                      <td className="p-3">
                        <button
                          onClick={() => handleDelete("zalo", t.phone)}
                          className="text-red-500 hover:text-red-700"
                          title="Xóa SĐT Zalo"
                        >
                          <IconTrash size={18} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        )}

      </main>
    </div>
  );
}
