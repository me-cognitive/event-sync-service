#!/usr/bin/env python3
"""One-time CDK app that creates the IAM identity used to deploy EventSyncStack.

Self-contained (single file) so it can be uploaded to AWS CloudShell, which is
already authenticated by the console login and needs no local access keys:

    pip install aws-cdk-lib constructs
    npx aws-cdk@2 bootstrap
    npx aws-cdk@2 deploy --app "python3 deployer_app.py"

Least privilege: the user cannot change resources itself. `cdk deploy` works by
assuming the roles created by `cdk bootstrap` (cdk-*-deploy-role etc.), so the
user only needs sts:AssumeRole on those, plus read/session permissions to
operate the deployed app. The access key is stored in Secrets Manager rather
than printed as a stack output.
"""

import os

import aws_cdk as cdk
from aws_cdk import CfnOutput, SecretValue, Stack, aws_iam as iam, aws_secretsmanager as secretsmanager
from constructs import Construct

USER_NAME = "cdk-deployer"
SECRET_NAME = "hexaware-poc/cdk-deployer"
APP_STACK = "EventSyncStack"


class DeployerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        account, region = self.account, self.region

        user = iam.User(self, "DeployerUser", user_name=USER_NAME)

        # Deploy: assume the CDK bootstrap roles (deploy, file-publishing, lookup).
        user.add_to_policy(iam.PolicyStatement(
            sid="AssumeCdkBootstrapRoles",
            actions=["sts:AssumeRole"],
            resources=[f"arn:aws:iam::{account}:role/cdk-*"],
        ))

        # Operate: resolve the Ubuntu AMI parameter, read the stack and its logs,
        # and open SSM shell sessions on the backend instance.
        user.add_to_policy(iam.PolicyStatement(
            sid="ReadUbuntuAmiParameters",
            actions=["ssm:GetParameter", "ssm:GetParameters"],
            resources=[f"arn:aws:ssm:{region}::parameter/aws/service/canonical/*"],
        ))
        user.add_to_policy(iam.PolicyStatement(
            sid="ReadAppStack",
            actions=["cloudformation:DescribeStacks", "cloudformation:DescribeStackEvents"],
            resources=[f"arn:aws:cloudformation:{region}:{account}:stack/{APP_STACK}/*"],
        ))
        user.add_to_policy(iam.PolicyStatement(
            sid="ReadAppLogs",
            actions=["logs:DescribeLogStreams", "logs:GetLogEvents", "logs:FilterLogEvents"],
            resources=[f"arn:aws:logs:{region}:{account}:log-group:{APP_STACK}-*"],
        ))
        user.add_to_policy(iam.PolicyStatement(
            sid="StartShellSessions",
            actions=["ssm:StartSession"],
            resources=[
                f"arn:aws:ec2:{region}:{account}:instance/*",
                f"arn:aws:ssm:{region}::document/SSM-SessionManagerRunShell",
            ],
        ))
        user.add_to_policy(iam.PolicyStatement(
            sid="ManageOwnSessions",
            actions=["ssm:TerminateSession", "ssm:ResumeSession"],
            resources=[f"arn:aws:ssm:{region}:{account}:session/{USER_NAME}-*"],
        ))
        user.add_to_policy(iam.PolicyStatement(
            sid="DescribeInstances",
            actions=["ec2:DescribeInstances", "ssm:DescribeInstanceInformation"],
            resources=["*"],  # describe calls don't support resource-level scoping
        ))

        key = iam.AccessKey(self, "DeployerAccessKey", user=user)
        secret = secretsmanager.Secret(
            self, "DeployerAccessKeySecret",
            secret_name=SECRET_NAME,
            description=f"Access key for the {USER_NAME} IAM user (EventSyncStack deployments)",
            secret_object_value={
                "aws_access_key_id": SecretValue.unsafe_plain_text(key.access_key_id),
                "aws_secret_access_key": key.secret_access_key,
            },
        )

        CfnOutput(self, "UserName", value=user.user_name)
        CfnOutput(self, "SecretName", value=secret.secret_name)
        CfnOutput(self, "ReadKeyCommand", value=(
            f"aws secretsmanager get-secret-value --secret-id {SECRET_NAME} "
            f"--region {region} --query SecretString --output text"
        ))


app = cdk.App()
DeployerStack(
    app,
    "EventSyncDeployer",
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION", "ap-southeast-2"),
    ),
    description="IAM user (least privilege) for deploying EventSyncStack with CDK",
)
app.synth()
