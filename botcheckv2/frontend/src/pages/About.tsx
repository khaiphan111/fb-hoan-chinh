// FB Live/Die Checker — Tác giả: @nhanxp | Hỗ trợ: Telegram/Facebook 
import { IconBrandFacebook, IconBrandTelegram, IconUserCircle } from "@tabler/icons-react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui";

export default function About() {
  return (
    <div className="flex flex-col gap-6 max-w-2xl">
      <h1 className="text-2xl font-semibold">Giới thiệu</h1>
      <Card>
        <CardHeader>
          <CardTitle>FB Live/Die Checker</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 text-sm">
          <p className="text-muted-foreground">
            Phần mềm theo dõi trạng thái live/die tài khoản Facebook và báo ngay qua bot
            Telegram khi có thay đổi. Mã nguồn mở, phục vụ cộng đồng nghiên cứu & phát triển.
          </p>
          <div className="flex items-center gap-2">
            <IconUserCircle size={18} />
            <span className="font-medium">Tác giả: @khaikhai998</span>
          </div>
          <div className="flex items-center gap-2">
            <IconBrandTelegram size={18} />
            <a
              className="font-medium underline"
              href="https://t.me/khaikhai998"
              target="_blank"
              rel="noreferrer"
            >
              Telegram: khaikhai998
            </a>
          </div>
          <div className="flex items-center gap-2">
            <IconBrandFacebook size={18} />
            <a
              className="font-medium underline"
              href="https://www.facebook.com/khaitradecoin"
              target="_blank"
              rel="noreferrer"
            >
              Facebook: PHAN VĂN KHẢI
            </a>
          </div>
          <p className="text-xs text-muted-foreground pt-2 border-t border-border">
            Liên hệ để được hỗ trợ hoặc nâng cấp tính năng theo nhu cầu.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
