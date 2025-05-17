from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware

from client import MCPClient

mcp_client_instance: MCPClient | None = None

# Lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    global mcp_client_instance
    # Initialize MCPClient
    print("Initializing MCPClient...")
    mcp_client_instance = MCPClient()
    # Connect to all configured servers
    try:
        await mcp_client_instance.connect_to_all_servers()
        print("MCPClient initialization and server connections complete.")
    except Exception as e:
        print(f"Error initializing MCPClient: {e}")
    
    yield
    
    # Shutdown logic
    if mcp_client_instance:
        print("Cleaning up MCPClient before shutdown...")
        await mcp_client_instance.cleanup()
        print("MCPClient cleaned up.")

app = FastAPI(lifespan=lifespan)

# CORS Middleware configuration
origins = [
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (GET, POST, etc.)
    allow_headers=["*"],  # Allows all headers
)

class ChatRequest(BaseModel):
    query: str

@app.post("/chat")
async def chat(request: ChatRequest):
    global mcp_client_instance
    if mcp_client_instance is None or not mcp_client_instance.sessions:
        raise HTTPException(status_code=500, detail="MCP Client not initialized properly or no servers connected.")
    try:
        response = await mcp_client_instance.process_query(request.query)
        return {"response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    print("Starting FastAPI server. Use a client (like Next.js app) to interact.")
    uvicorn.run(app, host="0.0.0.0", port=8000)