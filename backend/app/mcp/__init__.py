"""
Model Context Protocol (MCP) server package for LogShed.
Provides the in-process MCP server, tools, resources, and prompt definitions.
"""

from app.mcp.server import create_mcp_server

__all__ = ["create_mcp_server"]
