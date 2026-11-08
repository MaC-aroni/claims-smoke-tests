"""Before anything changes: are we pointed at the right AWS account, with working access?
Read-only. Fails if the credentials belong to a different account than the migration targets."""

import os
import sys

import boto3

PLACEHOLDER_ACCOUNT = "000000000000"


def main():
    expected = os.environ.get("MAC_TARGET_ACCOUNT", "")
    region = os.environ.get("MAC_TARGET_REGION") or os.environ.get("AWS_REGION", "us-east-1")
    identity = boto3.client("sts", region_name=region).get_caller_identity()
    print(f"credentials: {identity['Arn']}")
    print(f"account {identity['Account']}, region {region}, environment {os.environ.get('MAC_ENV', '?')}")
    if expected and expected != PLACEHOLDER_ACCOUNT and identity["Account"] != expected:
        sys.exit(f"wrong AWS account: migration targets {expected}, credentials are for {identity['Account']}")
    if expected == PLACEHOLDER_ACCOUNT:
        print("warning: migration.yaml still has the placeholder account ID")
    boto3.client("dynamodb", region_name=region).list_tables(Limit=1)
    boto3.client("lambda", region_name=region).list_functions(MaxItems=1)
    print("preflight passed: DynamoDB and Lambda are reachable")


if __name__ == "__main__":
    main()
