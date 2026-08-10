import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

import uvicorn
from backend.app.config import PORT

if __name__ == "__main__":
    print("")
    print("  ====================================")
    print("   TikTok Checker V2 -- @khaikhai998  ")
    print(f"  Dashboard: http://localhost:{PORT}      ")
    print("  ====================================")
    print("")

    uvicorn.run(
        "backend.app.main:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
        log_level="info",
    )
 