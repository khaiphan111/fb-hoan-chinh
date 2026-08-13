import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { api, getToken } from "../lib/api";
import { Card, CardContent, CardHeader, CardTitle, Button, Input } from "../components/ui";

export default function Referral() {
  const isUser = getToken()?.startsWith("user-");

  if (isUser) {
    return <UserReferral />;
  }
  return <AdminReferral />;
}

function UserReferral() {
  const [data, setData] = useState<any>(null);
  const [withdrawAmount, setWithdrawAmount] = useState("");
  const [bankName, setBankName] = useState("");
  const [accountNumber, setAccountNumber] = useState("");

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    try {
      setData(await api("/api/user/referral"));
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function handleWithdraw() {
    if (!withdrawAmount || !bankName || !accountNumber) {
      toast.error("Vui lòng điền đầy đủ thông tin rút tiền");
      return;
    }
    try {
      await api("/api/user/referral/withdraw", {
        method: "POST",
        body: JSON.stringify({ 
          amount: Number(withdrawAmount),
          bank_info: `${bankName} - ${accountNumber}`
        })
      });
      toast.success("Đã gửi yêu cầu rút tiền");
      setWithdrawAmount("");
      setBankName("");
      setAccountNumber("");
      loadData();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  if (!data) return <div>Đang tải...</div>;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Link Giới Thiệu</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2">
            <Input readOnly value={data.referral_link || ""} />
            <Button onClick={() => {
              navigator.clipboard.writeText(data.referral_link || "");
              toast.success("Đã copy link!");
            }}>
              Copy
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm font-normal">F1</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-bold">{data.f1_count || 0}</div></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm font-normal">F2</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-bold">{data.f2_count || 0}</div></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm font-normal">Hoa hồng chờ rút</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-bold text-green-500">{new Intl.NumberFormat("vi-VN").format(data.commission_available || 0)}đ</div></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm font-normal">Tổng hoa hồng</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-bold">{new Intl.NumberFormat("vi-VN").format(data.commission_total || 0)}đ</div></CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Chính Sách Hoa Hồng & Rút Tiền</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div>
            <strong>Chính sách VIP:</strong>
            <ul className="list-disc ml-5 mt-1">
              <li>Hạng Đồng (F1 nạp &lt; 5tr): 10%</li>
              <li>Hạng Bạc (F1 nạp &gt;= 5tr): 15%</li>
              <li>Hạng Vàng (F1 nạp &gt;= 20tr): 20%</li>
            </ul>
          </div>
          <div>
            <strong>Quy định rút tiền:</strong>
            <ul className="list-disc ml-5 mt-1">
              <li>Miễn phí 2 lần rút tiền mỗi tháng.</li>
              <li>Từ lần thứ 3 trở đi, phí rút tiền là 10,000 VNĐ.</li>
              <li>Bạn có thể dùng lệnh <code>/doitien</code> qua bot Telegram để chuyển hoa hồng thành số dư với bonus thêm 10%.</li>
              <li>Hoặc dùng bot Telegram <code>/ruttien</code> nếu bạn muốn rút nhanh hơn.</li>
            </ul>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Rút Tiền</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Input 
              placeholder="Tên ngân hàng (vd: Vietcombank)" 
              value={bankName} 
              onChange={(e) => setBankName(e.target.value)} 
            />
            <Input 
              placeholder="Số tài khoản" 
              value={accountNumber} 
              onChange={(e) => setAccountNumber(e.target.value)} 
            />
          </div>
          <div className="flex gap-2">
            <Input 
              type="number" 
              placeholder="Nhập số tiền..." 
              value={withdrawAmount} 
              onChange={(e) => setWithdrawAmount(e.target.value)} 
            />
            <Button onClick={handleWithdraw}>Rút tiền</Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Lịch Sử Hoa Hồng</CardTitle>
        </CardHeader>
        <CardContent>
          <table className="w-full text-left text-sm">
            <thead className="bg-muted">
              <tr>
                <th className="p-2">Thời gian</th>
                <th className="p-2">Loại</th>
                <th className="p-2">Số tiền</th>
                <th className="p-2">Trạng thái</th>
              </tr>
            </thead>
            <tbody>
              {data.history?.map((h: any) => (
                <tr key={h.id} className="border-b">
                  <td className="p-2">{new Date(h.created_at).toLocaleString()}</td>
                  <td className="p-2">{h.type}</td>
                  <td className="p-2 text-green-500">+{new Intl.NumberFormat("vi-VN").format(h.amount)}đ</td>
                  <td className="p-2">{h.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}

function AdminReferral() {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    try {
      setData(await api("/api/admin/referral"));
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function handleRequest(id: string, action: "approve" | "reject") {
    try {
      await api(`/api/admin/referral/withdraw/${id}`, {
        method: "POST",
        body: JSON.stringify({ action })
      });
      toast.success(action === "approve" ? "Đã duyệt" : "Đã từ chối");
      loadData();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  if (!data) return <div>Đang tải...</div>;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Bảng Xếp Hạng Giới Thiệu</CardTitle>
        </CardHeader>
        <CardContent>
          <table className="w-full text-left text-sm">
            <thead className="bg-muted">
              <tr>
                <th className="p-2">User ID</th>
                <th className="p-2">Tổng F1</th>
                <th className="p-2">Tổng F2</th>
                <th className="p-2">Tổng hoa hồng</th>
              </tr>
            </thead>
            <tbody>
              {data.leaderboard?.map((u: any, idx: number) => (
                <tr key={u.id || idx} className="border-b">
                  <td className="p-2">{u.user_id}</td>
                  <td className="p-2">{u.f1_count}</td>
                  <td className="p-2">{u.f2_count}</td>
                  <td className="p-2 text-green-500">{new Intl.NumberFormat("vi-VN").format(u.commission_total)}đ</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Yêu Cầu Rút Tiền</CardTitle>
        </CardHeader>
        <CardContent>
          <table className="w-full text-left text-sm">
            <thead className="bg-muted">
              <tr>
                <th className="p-2">User ID</th>
                <th className="p-2">Ngân hàng / STK</th>
                <th className="p-2">Phí (VNĐ)</th>
                <th className="p-2">Số tiền thực nhận</th>
                <th className="p-2">Thời gian</th>
                <th className="p-2 text-right">Hành động</th>
              </tr>
            </thead>
            <tbody>
              {data.history?.map((r: any) => (
                <tr key={r.id} className="border-b">
                  <td className="p-2">{r.tg_id}</td>
                  <td className="p-2">{r.bank_info}</td>
                  <td className="p-2">{new Intl.NumberFormat("vi-VN").format(r.fee || 0)}đ</td>
                  <td className="p-2 text-green-500">{new Intl.NumberFormat("vi-VN").format(r.amount - (r.fee || 0))}đ</td>
                  <td className="p-2">{new Date(r.created_at).toLocaleString()}</td>
                  <td className="p-2 text-right">
                    <div className="flex justify-end gap-2">
                      <Button size="sm" onClick={() => handleRequest(r.id, "approve")}>Duyệt</Button>
                      <Button size="sm" variant="outline" onClick={() => handleRequest(r.id, "reject")}>Từ chối</Button>
                    </div>
                  </td>
                </tr>
              ))}
              {!data.history?.length && (
                <tr>
                  <td colSpan={6} className="p-4 text-center text-muted-foreground">Không có yêu cầu nào</td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
