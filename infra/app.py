#!/usr/bin/env python3
import os

import aws_cdk as cdk

from stacks.event_sync_stack import EventSyncStack

app = cdk.App()
EventSyncStack(
    app,
    "EventSyncStack",
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION", "ap-southeast-2"),
    ),
    description="Event Sync Service: Angular UI on S3 + CloudFront, FastAPI backend in Docker on EC2 (Ubuntu), data in S3",
)
app.synth()
