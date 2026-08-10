// FB Live/Die Checker — Tác giả: @nhanxp | Hỗ trợ: Telegram/Facebook nhanxp
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function vnd(n: number | string): string {
  const v = Number(n) || 0;
  return v.toLocaleString("vi-VN");
}

export function fromNow(epoch: number): string {
  if (!epoch) return "—";
  const d = new Date(epoch * 1000);
  return d.toLocaleString("vi-VN");
}
