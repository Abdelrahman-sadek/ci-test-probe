"""MCP server: any MCP client (Claude Code, Claude Desktop, Cursor, VS Code) can search the library index,
ask grounded questions, and load every agent in agents/ as a prompt.

    claude mcp add agentkit -- agentkit --index /abs/path/data/index.json mcp
"""
from .agents import load_all


def _server_class():
    try:
        from mcp.server import MCPServer  # MCP Python SDK v2
        return MCPServer
    except ImportError:
        from mcp.server.fastmcp import FastMCP  # SDK v1
        return FastMCP


def build_server(index_path: str = "data/index.json"):
    server = _server_class()("agentkit")
    state: dict = {}

    def chat():
        if "chat" not in state:  # load lazily so the server starts even before an index exists
            from .cli import make_chat
            state["chat"] = make_chat(index_path)
        return state["chat"]

    @server.tool()
    def search_library(query: str, k: int = 5) -> list[dict]:
        """Hybrid (BM25 + character n-gram) search over the AUC Library index. Returns cited passages."""
        return [{"score": round(s, 4), "title": c.title, "section": c.section, "url": c.source, "text": c.text}
                for s, c in chat().index.search(query, k)]

    @server.tool()
    def ask_library(question: str) -> dict:
        """Ask the AUC Library assistant. Answers only from cited sources; refuses unsafe or out-of-scope asks."""
        return chat().ask(question).to_dict()

    @server.tool()
    def list_agents() -> list[dict]:
        """List the agent roster (name, division, model tier, when to use)."""
        return [{"name": a.name, "division": a.division, "model": a.model, "description": a.description}
                for a in load_all().values()]

    for agent in load_all().values():
        def make(body: str):
            def prompt(task: str = "") -> str:
                return body + (f"\n\n## Task\n{task}" if task else "")
            return prompt
        server.prompt(name=agent.name, description=agent.description)(make(agent.body))
    return server
