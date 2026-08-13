import toast from "react-hot-toast";

export const showRealtimeAlert = (message: string, type: "success" | "error" | "info" = "info") => {
  if (type === "success") {
    toast.success(message);
  } else if (type === "error") {
    toast.error(message);
  } else {
    toast(message, {
      icon: '🔔',
    });
  }
};

export function RealtimeToastContainer() {
  return null;
}
