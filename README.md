# MCP Client

A Python FastAPI server that connects to various Model Context Protocol (MCP) servers and provides a unified interface for AI-powered tools.

## Installation

### Requirements
- Python 3.11 or higher
- Node.js and npm (for MCP servers)

### Setup

1. Clone the repository

2. Create and activate a virtual environment:
```bash
cd mcp-client
python -m venv .venv
# On Windows
.venv\Scripts\activate
# On macOS/Linux
source .venv/bin/activate
```

3. Install dependencies:
```bash
pip install -e .
```

4. Create a `.env` file in the root directory with your Anthropic API key:
```
ANTHROPIC_API_KEY=your_api_key_here
```

## Running the Application

1. Start the FastAPI server:
```bash
python main.py
```
The server will run on http://localhost:8000

2. Connect a frontend client to the API endpoint:
   - POST to `/chat` with a JSON body: `{"query": "your question here"}`

## Configuration

Server configurations are defined in `server_configs.json`. The client will automatically connect to all configured MCP servers on startup.

## API Endpoints

- `POST /chat`: Send a query to process through the connected MCP servers
  - Request body: `{"query": "string"}`
  - Response: `{"response": "string"}`
