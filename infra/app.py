#!/usr/bin/env python3
import os

import aws_cdk as cdk
from aws_cdk import aws_iam as iam

from stacks.event_sync_stack import EventSyncStack

# Created once by foundation_app.py; the CDK execution policy only allows
# creating roles that carry it.
PERMISSIONS_BOUNDARY = "EventSyncPermissionsBoundary"

app = cdk.App()
stack = EventSyncStack(
    app,
    "EventSyncStack",
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION", "ap-southeast-2"),
    ),
    description="Event Sync Service: Angular UI on S3 + CloudFront, FastAPI backend in Docker on EC2 (Ubuntu), data in S3",
)
iam.PermissionsBoundary.of(stack).apply(
    iam.ManagedPolicy.from_managed_policy_name(stack, "PermissionsBoundary", PERMISSIONS_BOUNDARY)
)
app.synth()
