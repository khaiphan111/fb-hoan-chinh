import { useState, useEffect } from "react";
import toast from "react-hot-toast";
import { IconEdit, IconTrash, IconPlus, IconList, IconHistory } from "@tabler/icons-react";
import { api } from "../lib/api";
import { Button, Card, CardContent, Input, Label, Badge } from "../components/ui";

export default function Admins() {
  const [tab, setTab] = useState<"list" | "audit">("list");
  const [admins, setAdmins] = useState<any[]>([]);
  const [auditLogs, setAuditLogs] = useState<any[]>([]);
  
  const [loading, setLoading] = useState(false);
  const [editingAdmin, setEditingAdmin] = useState<any>(null);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("admin");

  useEffect(() => {
    if (tab === "list") fetchAdmins();
    else fetchAuditLogs();
  }, [tab]);

  async function fetchAdmins() {
    setLoading(true);
    try {
      const data = await api("/api/admins");
      setAdmins(Array.isArray(data) ? data : data.admins || []);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function fetchAuditLogs() {
    setLoading(true);
    try {
      const data = await api("/api/admin/audit-logs");
      setAuditLogs(Array.isArray(data) ? data : data.logs || []);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  }

  function handleEdit(admin: any) {
    setEditingAdmin(admin);
    setUsername(admin.username || "");
    setPassword("");
    setRole(admin.role || "admin");
  }

  function handleCreate() {
    setEditingAdmin({});
    setUsername("");
    setPassword("");
    setRole("admin");
  }

  async function saveAdmin(e: React.FormEvent) {
    e.preventDefault();
    try {
      if (editingAdmin.id) {
        await api(`/api/admins/${editingAdmin.id}`, {
          method: "PUT",
          body: JSON.stringify({ username, password: password || undefined, role })
        });
        toast.success("Cập nhật thành công");
      } else {
        await api(`/api/admins`, {
          method: "POST",
          body: JSON.stringify({ username, password, role })
        });
        toast.success("Tạo mới thành công");
      }
      setEditingAdmin(null);
      fetchAdmins();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function deleteAdmin(id: number) {
    if (!confirm("Bạn có chắc muốn xóa admin này?")) return;
    try {
      await api(`/api/admins/${id}`, { method: "DELETE" });
      toast.success("Đã xóa");
      fetchAdmins();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  const roleLabels: Record<string, string> = {
    super_admin: "Super Admin",
    admin: "Admin",
    moderator: "Moderator"
  };

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-bold">Quản lý Admin</h1>
      
      <div className="flex gap-2 border-b border-border mb-4">
        <button
          onClick={() => setTab("list")}
          className={`px-4 py-2 flex items-center gap-2 border-b-2 font-medium ${tab === "list" ? "border-foreground text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}
        >
          <IconList size={18} /> Danh sách
        </button>
        <button
          onClick={() => setTab("audit")}
          className={`px-4 py-2 flex items-center gap-2 border-b-2 font-medium ${tab === "audit" ? "border-foreground text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}
        >
          <IconHistory size={18} /> Audit Log
        </button>
      </div>

      {tab === "list" && (
        <div className="flex flex-col gap-4">
          {!editingAdmin ? (
            <Card>
              <div className="flex justify-between items-center p-4 border-b border-border">
                <h2 className="text-lg font-semibold">Tài khoản quản trị</h2>
                <Button onClick={handleCreate} size="sm"><IconPlus size={16} /> Thêm mới</Button>
              </div>
              <CardContent className="p-0 overflow-x-auto">
                <table className="w-full text-left text-sm whitespace-nowrap">
                  <thead>
                    <tr className="border-b border-border bg-muted/50">
                      <th className="px-4 py-3 font-medium">ID</th>
                      <th className="px-4 py-3 font-medium">Tên đăng nhập</th>
                      <th className="px-4 py-3 font-medium">Vai trò</th>
                      <th className="px-4 py-3 font-medium text-right">Thao tác</th>
                    </tr>
                  </thead>
                  <tbody>
                    {admins.map(a => (
                      <tr key={a.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                        <td className="px-4 py-3">{a.id}</td>
                        <td className="px-4 py-3 font-medium">{a.username}</td>
                        <td className="px-4 py-3">
                          <Badge className="bg-primary/10 text-primary border-primary/20">
                            {roleLabels[a.role] || a.role}
                          </Badge>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <div className="flex justify-end gap-2">
                            <Button variant="outline" size="icon" onClick={() => handleEdit(a)}>
                              <IconEdit size={16} />
                            </Button>
                            <Button variant="danger" size="icon" onClick={() => deleteAdmin(a.id)}>
                              <IconTrash size={16} />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                    {admins.length === 0 && (
                      <tr>
                        <td colSpan={4} className="px-4 py-8 text-center text-muted-foreground">
                          {loading ? "Đang tải..." : "Chưa có dữ liệu"}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          ) : (
            <Card>
              <div className="p-4 border-b border-border">
                <h2 className="text-lg font-semibold">{editingAdmin.id ? "Sửa admin" : "Thêm admin mới"}</h2>
              </div>
              <CardContent>
                <form onSubmit={saveAdmin} className="flex flex-col gap-4 max-w-md">
                  <div className="flex flex-col gap-1.5">
                    <Label>Tên đăng nhập</Label>
                    <Input 
                      value={username}
                      onChange={e => setUsername(e.target.value)}
                      required
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label>Mật khẩu {editingAdmin.id && <span className="text-muted-foreground font-normal">(Để trống nếu không đổi)</span>}</Label>
                    <Input 
                      type="password"
                      value={password}
                      onChange={e => setPassword(e.target.value)}
                      required={!editingAdmin.id}
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label>Vai trò</Label>
                    <select 
                      className="flex h-10 w-full rounded-md border border-border bg-transparent px-3 text-sm outline-none focus:border-foreground/40 transition-colors"
                      value={role}
                      onChange={e => setRole(e.target.value)}
                    >
                      <option className="text-foreground bg-background" value="super_admin">Super Admin</option>
                      <option className="text-foreground bg-background" value="admin">Admin</option>
                      <option className="text-foreground bg-background" value="moderator">Moderator</option>
                    </select>
                  </div>
                  <div className="flex gap-2 mt-2">
                    <Button type="submit">Lưu</Button>
                    <Button type="button" variant="outline" onClick={() => setEditingAdmin(null)}>Hủy</Button>
                  </div>
                </form>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {tab === "audit" && (
        <Card>
          <CardContent className="p-0 overflow-x-auto">
            <table className="w-full text-left text-sm whitespace-nowrap">
              <thead>
                <tr className="border-b border-border bg-muted/50">
                  <th className="px-4 py-3 font-medium">Thời gian</th>
                  <th className="px-4 py-3 font-medium">Admin ID</th>
                  <th className="px-4 py-3 font-medium">Hành động</th>
                  <th className="px-4 py-3 font-medium">Mục tiêu</th>
                  <th className="px-4 py-3 font-medium">Chi tiết</th>
                </tr>
              </thead>
              <tbody>
                {auditLogs.map(l => (
                  <tr key={l.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                    <td className="px-4 py-3">{new Date(l.created_at * 1000).toLocaleString()}</td>
                    <td className="px-4 py-3 font-medium">{l.admin_id}</td>
                    <td className="px-4 py-3">
                      <Badge status="neutral">{l.action}</Badge>
                    </td>
                    <td className="px-4 py-3">{l.target}</td>
                    <td className="px-4 py-3">{l.details}</td>
                  </tr>
                ))}
                {auditLogs.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">
                      {loading ? "Đang tải..." : "Chưa có dữ liệu"}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
