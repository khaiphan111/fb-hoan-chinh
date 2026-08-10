// FB Live/Die Checker — Tác giả: @nhanxp | Hỗ trợ: Telegram/Facebook nhanxp
import { useEffect, useState } from "react";

const KEY = "fbc_theme";

export function useTheme() {
  const [dark, setDark] = useState(() => localStorage.getItem(KEY) === "dark");

  useEffect(() => {
    const root = document.documentElement;
    if (dark) root.classList.add("dark");
    else root.classList.remove("dark");
    localStorage.setItem(KEY, dark ? "dark" : "light");
  }, [dark]);

  return { dark, toggle: () => setDark((v) => !v) };
}
