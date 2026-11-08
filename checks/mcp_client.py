"""Calls an MCP server behind a Lambda function URL with AWS_IAM auth: each JSON-RPC message is
an AWS SigV4-signed POST. Without a grant on the MCP function, AWS answers 403."""

import json
import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

PROTOCOL_VERSION = "2025-06-18"


class McpError(Exception):
    pass


class McpClient:
    def __init__(self, url: str, region: str, session=None, timeout: int = 10):
        self.url = url
        self.region = region
        self.session = session or boto3.Session()
        self.timeout = timeout
        self._next_id = 0

    def _post(self, message: dict) -> dict | None:
        body = json.dumps(message)
        request = AWSRequest(method="POST", url=self.url, data=body, headers={
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION})
        SigV4Auth(self.session.get_credentials().get_frozen_credentials(), "lambda", self.region).add_auth(request)
        http = urllib.request.Request(self.url, data=body.encode("utf-8"), method="POST", headers=dict(request.headers))
        try:
            with urllib.request.urlopen(http, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            hint = " (is this role granted access to the MCP tool?)" if e.code == 403 else ""
            raise McpError(f"MCP call failed: HTTP {e.code}{hint}: {detail}") from e
        return json.loads(raw) if raw else None

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        reply = self._post({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}})
        if "error" in reply:
            raise McpError(f"MCP {method} error {reply['error']['code']}: {reply['error']['message']}")
        return reply["result"]

    def initialize(self, client_name: str) -> dict:
        result = self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                             "clientInfo": {"name": client_name, "version": "1.0"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def call_tool(self, name: str, arguments: dict) -> dict:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise McpError(f"MCP tool {name} failed: {result['content'][0]['text']}")
        return result.get("structuredContent") or json.loads(result["content"][0]["text"])
