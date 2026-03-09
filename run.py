"""One-click launcher for Agent OS."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "my_agent_os.api_gateway.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
