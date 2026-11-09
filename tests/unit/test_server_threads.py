"""Listing tools run off the MCP event-loop thread."""

import asyncio
import threading

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bridge import server


class StubBridge:
    def __init__(self, error=None):
        self.error = error
        self.threads = {}

    def _call(self, name):
        self.threads[name] = threading.get_ident()
        if self.error:
            raise self.error
        return {"name": name}

    def list_accounts(self):
        return self._call("list_accounts")

    def usage_report(self, account=None):
        return self._call(f"usage_report:{account}")

    def list_agents(self):
        return self._call("list_agents")


# @pt.id KIT-PERF-MCP-001-to-thread
# @pt.item MCP listing tools threading
# @pt.condition Listing tools are awaited with a stub bridge, with and without a ValueError
# @pt.expected Work runs off the event-loop thread, returns the stub value, errors become ToolError
# @pt.target bridge.server listing tools
# @pt.method unit
# @pt.legacy none
def test_listing_tools_use_threads(monkeypatch):
    stub = StubBridge()
    monkeypatch.setattr(server, "bridge", lambda: stub)

    async def run():
        loop_thread = threading.get_ident()
        results = [await server.bridge_list_accounts(),
                   await server.bridge_account_usage("work"),
                   await server.bridge_list_agents()]
        return loop_thread, results

    loop_thread, results = asyncio.run(run())
    assert results == [{"name": "list_accounts"}, {"name": "usage_report:work"},
                       {"name": "list_agents"}]
    assert len(stub.threads) == 3
    assert all(ident != loop_thread for ident in stub.threads.values())

    failing = StubBridge(ValueError("bad account"))
    monkeypatch.setattr(server, "bridge", lambda: failing)
    for tool in (server.bridge_list_accounts, server.bridge_account_usage,
                 server.bridge_list_agents):
        with pytest.raises(ToolError, match="bad account"):
            asyncio.run(tool())
