import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { hasError: boolean; error: any }> {
  constructor(props: any) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: any) {
    return { hasError: true, error };
  }

  componentDidCatch(error: any, errorInfo: any) {
    console.error("ErrorBoundary caught an error", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen flex flex-col items-center justify-center p-6 text-center">
          <h2 className="text-xl font-bold mb-2">Đã xảy ra lỗi giao diện</h2>
          <p className="text-muted-foreground mb-4 max-w-md">
            {this.state.error?.message || "Vui lòng xóa bộ nhớ tạm (Cache) hoặc đăng nhập lại."}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => {
                localStorage.clear();
                window.location.href = "/";
              }}
              className="px-4 py-2 bg-black text-white rounded-md hover:opacity-80 transition-opacity"
            >
              Xóa Cache & Đăng nhập lại
            </button>
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 border border-gray-300 rounded-md hover:bg-gray-100"
            >
              Tải lại trang
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);
