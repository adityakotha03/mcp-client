import asyncio
import json
import os
from typing import Optional, Dict, List, Any
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters, Tool
from mcp.client.stdio import stdio_client

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

class MCPClient:
    def __init__(self):
        self.sessions: Dict[str, ClientSession] = {}
        self.tool_to_session_map: Dict[str, str] = {}
        self.exit_stack = AsyncExitStack()
        self.anthropic = Anthropic()
        self.server_configs = self._load_server_configs()
        self.conversation_history = []
        
    def _load_server_configs(self):
        config_path = os.path.join(os.path.dirname(__file__), 'server_configs.json')
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading server configs from {config_path}: {e}")
            return {}
    
    async def connect_to_all_servers(self):
        if not self.server_configs:
            print("No server configurations loaded. Skipping connections.")
            return

        for server_identifier, config in self.server_configs.items():
            print(f"Attempting to connect to server: {server_identifier}...")
            try:
                command = config.get("command")
                args = config.get("args")

                if not command or not args:
                    print(f"Skipping server {server_identifier} due to missing 'command' or 'args' in config.")
                    continue

                server_params = StdioServerParameters(command=command, args=args, env=None)
                
                stdio, write = await self.exit_stack.enter_async_context(stdio_client(server_params))
                session = await self.exit_stack.enter_async_context(ClientSession(stdio, write))
                
                await session.initialize()
                self.sessions[server_identifier] = session
                print(f"Successfully connected to server: {server_identifier}")

                # List tools and populate tool_to_session_map
                response = await session.list_tools()
                server_tools = response.tools
                for tool in server_tools:
                    if tool.name in self.tool_to_session_map:
                        print(f"Warning: Tool name '{tool.name}' from server '{server_identifier}' conflicts with a tool from server '{self.tool_to_session_map[tool.name]}'. The one from '{server_identifier}' will be used.")
                    self.tool_to_session_map[tool.name] = server_identifier
                print(f"Server {server_identifier} provides tools: {[tool.name for tool in server_tools]}")

            except Exception as e:
                print(f"Error connecting to or initializing server {server_identifier}: {e}")
        
        if self.sessions:
            print(f"\nConnected to {len(self.sessions)} server(s). Total unique tools available: {len(self.tool_to_session_map)}")
        else:
            print("\nNo servers connected.")

    async def _get_all_available_tools_for_claude(self) -> List[Dict[str, Any]]:
        """Aggregates tools from all connected servers for Claude."""
        claude_tools = []
        processed_tool_names = set()

        for server_id, session in self.sessions.items():
            try:
                response = await session.list_tools()
                for tool_spec in response.tools:
                    if tool_spec.name not in processed_tool_names:
                         # Check if tool_spec is an instance of the expected Tool class
                        if hasattr(tool_spec, 'name') and hasattr(tool_spec, 'description') and hasattr(tool_spec, 'inputSchema'):
                            claude_tools.append({
                                "name": tool_spec.name,
                                "description": tool_spec.description,
                                "input_schema": tool_spec.inputSchema
                            })
                            processed_tool_names.add(tool_spec.name)
                        else:
                            print(f"Warning: Tool '{getattr(tool_spec, 'name', 'Unknown')}' from server '{server_id}' has unexpected structure and will be skipped.")
            except Exception as e:
                print(f"Error listing tools for server {server_id}: {e}")
        return claude_tools

    async def process_query(self, query: str) -> str:
        messages = list(self.conversation_history)
        messages.append({"role": "user", "content": query})

        available_tools_for_claude = await self._get_all_available_tools_for_claude()

        # Initial Claude API call
        response_from_claude = self.anthropic.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=2000,
            messages=messages,
            tools=available_tools_for_claude if available_tools_for_claude else None
        )

        final_text_parts = []
        current_assistant_content_for_history = []

        while True:
            if not response_from_claude.content:
                break 

            stop_reason = response_from_claude.stop_reason
            
            assistant_turn_has_tool_calls = False
            new_tool_calls_this_turn = []

            for content_block in response_from_claude.content:
                if content_block.type == 'text':
                    final_text_parts.append(content_block.text)
                    current_assistant_content_for_history.append({"type": "text", "text": content_block.text})
                
                elif content_block.type == 'tool_use':
                    assistant_turn_has_tool_calls = True
                    tool_name = content_block.name
                    tool_input = content_block.input
                    tool_use_id = content_block.id
                    
                    final_text_parts.append(f"[Claude wants to use tool: {tool_name} with args: {tool_input}]")
                    current_assistant_content_for_history.append({
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": tool_name,
                        "input": tool_input
                    })
                    new_tool_calls_this_turn.append(content_block)

            if current_assistant_content_for_history:
                messages.append({
                    "role": "assistant",
                    "content": list(current_assistant_content_for_history)
                })
                current_assistant_content_for_history = []

            if not assistant_turn_has_tool_calls:
                break

            tool_results_for_claude = []
            for tool_call_block in new_tool_calls_this_turn:
                tool_name = tool_call_block.name
                tool_input = tool_call_block.input
                tool_use_id = tool_call_block.id

                target_server_id = self.tool_to_session_map.get(tool_name)
                if not target_server_id:
                    print(f"Error: Tool '{tool_name}' requested by Claude but not found in any connected server.")
                    tool_results_for_claude.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": [{"type": "text", "text": f"Error: Tool '{tool_name}' is not available."}],
                        "is_error": True
                    })
                    final_text_parts.append(f"[Error: Tool {tool_name} not found by client]")
                    continue

                target_session = self.sessions.get(target_server_id)
                if not target_session:
                    print(f"Error: Server '{target_server_id}' for tool '{tool_name}' not found in active sessions.")
                    tool_results_for_claude.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": [{"type": "text", "text": f"Error: Client-side issue, server for tool '{tool_name}' unavailable."}],
                        "is_error": True
                    })
                    final_text_parts.append(f"[Error: Server for {tool_name} unavailable]")
                    continue
                
                try:
                    final_text_parts.append(f"[Client calling tool {tool_name} on server {target_server_id} with args {tool_input}]")
                    tool_result_mcp = await target_session.call_tool(tool_name, tool_input)
                    tool_result_content_for_claude = tool_result_mcp.content 
                    
                    tool_results_for_claude.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": tool_result_content_for_claude
                    })
                    final_text_parts.append(f"[Tool {tool_name} result: {tool_result_content_for_claude}]")
                except Exception as e:
                    print(f"Error calling tool {tool_name} on server {target_server_id}: {e}")
                    tool_results_for_claude.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": [{"type": "text", "text": f"Error executing tool {tool_name}: {str(e)}"}],
                        "is_error": True
                    })
                    final_text_parts.append(f"[Error executing tool {tool_name}: {str(e)}]")

            if tool_results_for_claude:
                messages.append({
                    "role": "user",
                    "content": tool_results_for_claude
                })
            
            response_from_claude = self.anthropic.messages.create(
                model="claude-3-5-sonnet-20240620",
                max_tokens=2000,
                messages=messages,
                tools=available_tools_for_claude if available_tools_for_claude else None
            )

        self.conversation_history = list(messages)
        return "\n".join(final_text_parts)
            
    async def cleanup(self):
        await self.exit_stack.aclose()
        print(f"MCPClient resources cleaned up. {len(self.sessions)} session(s) closed.")
        self.sessions.clear()
        self.tool_to_session_map.clear()
        self.conversation_history.clear()

async def standalone_chat_loop(client: MCPClient):
    print("\nMCP Client Standalone Chat Started!")
    print("Type your queries or 'quit' to exit.")
    
    while True:
        try:
            query = input("\nQuery: ").strip()
            if query.lower() == 'quit':
                break
            response = await client.process_query(query)
            print("\nClaude:" + response)
        except KeyboardInterrupt:
            print("\nExiting chat loop...")
            break
        except Exception as e:
            print(f"\nError in chat loop: {str(e)}")

async def main_standalone():
    client = MCPClient()
    try:
        await client.connect_to_all_servers()
        if not client.sessions:
            print("No servers connected. Exiting standalone chat.")
            return
        await standalone_chat_loop(client)
    finally:
        await client.cleanup()

if __name__ == "__main__":
    print("Running MCPClient in standalone mode for testing...")
    asyncio.run(main_standalone())