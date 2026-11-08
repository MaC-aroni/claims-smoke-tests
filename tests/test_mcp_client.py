import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "checks"))

from mcp_client import McpClient, McpError  # noqa: E402


@pytest.fixture
def server():
    """A minimal stateless MCP server that records what it receives."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"auth": self.headers.get("Authorization", ""), "message": body,
                         "version": self.headers.get("MCP-Protocol-Version")})
            if self.path == "/forbidden":
                return self._reply(403, {"Message": "Forbidden. For troubleshooting Function URL authorization issues"})
            if "id" not in body:
                return self._reply(202, None)
            method = body["method"]
            result = {
                "initialize": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                               "serverInfo": {"name": "claims-mcp", "version": "1.4.0"}},
                "tools/list": {"tools": [{"name": "check_eligibility"}]},
                "tools/call": {"content": [{"type": "text", "text": "{}"}], "isError": False,
                               "structuredContent": {"eligible": True, "reason": "active coverage"}},
            }.get(method)
            if result is None:
                return self._reply(200, {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "nope"}})
            self._reply(200, {"jsonrpc": "2.0", "id": body["id"], "result": result})

        def _reply(self, status, payload):
            data = json.dumps(payload).encode() if payload is not None else b""
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", seen
    httpd.shutdown()


@pytest.fixture
def session():
    return boto3.Session(aws_access_key_id="AKIATEST", aws_secret_access_key="secret", region_name="us-east-1")


def test_initialize_list_and_call(server, session):
    url, seen = server
    client = McpClient(url + "/", "us-east-1", session=session)
    assert client.initialize("test")["serverInfo"]["name"] == "claims-mcp"
    assert client.request("tools/list")["tools"][0]["name"] == "check_eligibility"
    assert client.call_tool("check_eligibility", {"member_id": "M-1", "service_date": "2024-01-01"})["eligible"]
    methods = [s["message"].get("method") for s in seen]
    assert methods == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    assert [s["message"].get("id") for s in seen] == [1, None, 2, 3]


def test_every_request_is_sigv4_signed_for_lambda(server, session):
    url, seen = server
    McpClient(url + "/", "us-east-1", session=session).request("tools/list")
    auth = seen[0]["auth"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIATEST/") and "/us-east-1/lambda/aws4_request" in auth
    assert seen[0]["version"] == "2025-06-18"


def test_403_explains_the_missing_grant(server, session):
    url, _ = server
    with pytest.raises(McpError, match="HTTP 403 \\(is this role granted access to the MCP tool\\?\\)"):
        McpClient(url + "/forbidden", "us-east-1", session=session).request("tools/list")


def test_json_rpc_errors_raise(server, session):
    url, _ = server
    with pytest.raises(McpError, match="error -32601"):
        McpClient(url + "/", "us-east-1", session=session).request("resources/list")
