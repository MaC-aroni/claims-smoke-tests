"""After the deploy: does the migrated system actually work, end to end?

1. The Claims MCP tool answers like an MCP server (initialize, tools/list).
2. ClaimsAdjudicationService is up.
3. Adjudicating a real migrated claim works. That needs the service to read the claims table
   and call the MCP tool, so it also proves the MCP access grant.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp_client import McpClient, McpError  # noqa: E402

SAMPLE_CLAIM = "CLM-1001"


def signed(url: str, method: str, region: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body) if body is not None else None
    request = AWSRequest(method=method, url=url, data=data, headers={"Content-Type": "application/json"})
    SigV4Auth(boto3.Session().get_credentials().get_frozen_credentials(), "lambda", region).add_auth(request)
    http = urllib.request.Request(url, data=data.encode() if data else None, method=method, headers=dict(request.headers))
    try:
        with urllib.request.urlopen(http, timeout=20) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"error": raw.decode("utf-8", "replace")[:300]}


def main():
    env = os.environ.get("MAC_ENV", "rehearsal")
    region = os.environ.get("MAC_TARGET_REGION") or os.environ.get("AWS_REGION", "us-east-1")
    prefix = f"mac-claims-{env}"
    lam = boto3.client("lambda", region_name=region)
    mcp_url = lam.get_function_url_config(FunctionName=f"{prefix}-mcp")["FunctionUrl"]
    service_url = lam.get_function_url_config(FunctionName=f"{prefix}-service")["FunctionUrl"].rstrip("/")

    mcp = McpClient(mcp_url, region)
    try:
        info = mcp.initialize("mac-smoke-test")
        tools = [t["name"] for t in mcp.request("tools/list")["tools"]]
    except McpError as e:
        sys.exit(f"Claims MCP tool isn't working: {e}")
    print(f"MCP tool {info['serverInfo']['name']} {info['serverInfo']['version']} "
          f"(protocol {info['protocolVersion']}) offers: {', '.join(tools)}")
    if "check_eligibility" not in tools:
        sys.exit("Claims MCP tool doesn't offer check_eligibility")

    status, body = signed(f"{service_url}/health", "GET", region)
    if status != 200:
        sys.exit(f"ClaimsAdjudicationService health check: HTTP {status} {body}")
    print("ClaimsAdjudicationService is up")

    status, body = signed(f"{service_url}/adjudicate", "POST", region, {"claim_id": SAMPLE_CLAIM})
    print(f"POST /adjudicate {SAMPLE_CLAIM} -> HTTP {status}: {json.dumps(body)}")
    if status != 200:
        sys.exit(f"adjudicating {SAMPLE_CLAIM} failed: {body.get('error', body)}")
    if body.get("decision") not in ("approved", "denied", "pending_review"):
        sys.exit(f"unexpected decision: {body}")
    print(f"smoke test passed: {SAMPLE_CLAIM} was {body['decision']} ({body['reason']})")


if __name__ == "__main__":
    main()
