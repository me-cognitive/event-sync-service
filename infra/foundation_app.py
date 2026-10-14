#!/usr/bin/env python3
"""One-time account foundation for deploying EventSyncStack without long-lived keys.

Run once by an administrator (from AWS CloudShell or an SSO admin session):

    pip install aws-cdk-lib constructs
    npx aws-cdk@2 deploy --app "python3 foundation_app.py" -c githubRepo=<owner>/<repo>

It creates:
  * GitHub Actions OIDC trust + `EventSyncGitHubDeployRole`: assumable only by
    workflows of <owner>/<repo> running in its `production` environment, and only
    allowed to assume the CDK bootstrap roles. No secrets are stored anywhere.
  * `EventSyncCdkExecutionPolicy`: replaces AdministratorAccess as the policy
    CloudFormation uses (pass it to `cdk bootstrap --cloudformation-execution-policies`).
    Scoped to the services and resource-name prefixes the app uses.
  * `EventSyncPermissionsBoundary`: every IAM role the app creates must carry it,
    so a deployment can never create a role more powerful than the app needs.

This stack deploys with the caller's own credentials (no CDK bootstrap needed),
because the bootstrap itself depends on the execution policy defined here.
"""

import aws_cdk as cdk
from aws_cdk import CfnOutput, Stack, aws_iam as iam
from constructs import Construct

APP_STACK = "EventSyncStack"
APP_PREFIX = APP_STACK.lower()  # CDK-generated bucket names are lower-case
CDK_QUALIFIER = "hnb659fds"  # default `cdk bootstrap` qualifier
EXECUTION_POLICY = "EventSyncCdkExecutionPolicy"
BOUNDARY_POLICY = "EventSyncPermissionsBoundary"
DEPLOY_ROLE = "EventSyncGitHubDeployRole"
GITHUB_OIDC_URL = "token.actions.githubusercontent.com"


class FoundationStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, github_repo: str | None, github_environment: str,
                 existing_oidc_provider: bool, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        account, region = self.account, self.region
        boundary_arn = f"arn:aws:iam::{account}:policy/{BOUNDARY_POLICY}"

        # --- Permissions boundary: the most any app-created role (EC2 instance,
        # deployment Lambdas) can ever do. Grants nothing by itself.
        iam.ManagedPolicy(
            self, "PermissionsBoundary",
            managed_policy_name=BOUNDARY_POLICY,
            description="Upper bound for IAM roles created by EventSyncStack",
            statements=[
                iam.PolicyStatement(
                    sid="AppRuntimeServices",
                    actions=["s3:*", "logs:*", "ssm:*", "ssmmessages:*", "ec2messages:*",
                             "cloudfront:CreateInvalidation", "cloudfront:GetInvalidation", "ec2:Describe*"],
                    resources=["*"],
                ),
            ],
        )

        # --- CloudFormation execution policy (used instead of AdministratorAccess).
        role_arns = [f"arn:aws:iam::{account}:role/{APP_STACK}-*",
                     f"arn:aws:iam::{account}:instance-profile/{APP_STACK}-*"]
        iam.ManagedPolicy(
            self, "CdkExecutionPolicy",
            managed_policy_name=EXECUTION_POLICY,
            description="What CloudFormation may do when deploying EventSyncStack (CDK bootstrap execution policy)",
            statements=[
                iam.PolicyStatement(
                    sid="AppServices",
                    actions=["cloudfront:*", "ec2:*", "lambda:*", "logs:*"],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    sid="AppBuckets",
                    actions=["s3:*"],
                    resources=[f"arn:aws:s3:::{APP_PREFIX}-*", f"arn:aws:s3:::{APP_PREFIX}-*/*"],
                ),
                iam.PolicyStatement(
                    sid="ReadCdkAssets",  # Lambda code for deployment helpers lives in the bootstrap bucket
                    actions=["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"],
                    resources=[f"arn:aws:s3:::cdk-{CDK_QUALIFIER}-assets-{account}-{region}",
                               f"arn:aws:s3:::cdk-{CDK_QUALIFIER}-assets-{account}-{region}/*"],
                ),
                iam.PolicyStatement(
                    sid="ResolveParameters",  # Ubuntu AMI + CDK bootstrap version
                    actions=["ssm:GetParameter", "ssm:GetParameters"],
                    resources=[f"arn:aws:ssm:{region}::parameter/aws/service/canonical/*",
                               f"arn:aws:ssm:{region}:{account}:parameter/cdk-bootstrap/{CDK_QUALIFIER}/*"],
                ),
                iam.PolicyStatement(
                    sid="CreateRolesOnlyWithBoundary",
                    actions=["iam:CreateRole", "iam:PutRolePermissionsBoundary"],
                    resources=[role_arns[0]],
                    conditions={"StringEquals": {"iam:PermissionsBoundary": boundary_arn}},
                ),
                iam.PolicyStatement(
                    sid="ManageAppRoles",
                    actions=["iam:GetRole", "iam:DeleteRole", "iam:TagRole", "iam:UntagRole",
                             "iam:UpdateAssumeRolePolicy", "iam:PutRolePolicy", "iam:GetRolePolicy",
                             "iam:DeleteRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy",
                             "iam:PassRole", "iam:CreateInstanceProfile", "iam:DeleteInstanceProfile",
                             "iam:GetInstanceProfile", "iam:AddRoleToInstanceProfile",
                             "iam:RemoveRoleFromInstanceProfile", "iam:TagInstanceProfile"],
                    resources=role_arns,
                ),
                iam.PolicyStatement(
                    sid="NeverRemoveBoundary",
                    effect=iam.Effect.DENY,
                    actions=["iam:DeleteRolePermissionsBoundary"],
                    resources=["*"],
                ),
            ],
        )

        CfnOutput(self, "BootstrapCommand", value=(
            f"npx aws-cdk@2 bootstrap aws://{account}/{region} "
            f"--cloudformation-execution-policies arn:aws:iam::{account}:policy/{EXECUTION_POLICY}"
        ))

        # --- GitHub Actions OIDC federation (skipped with -c githubOidc=false, e.g. when an
        # organization SCP forbids creating identity providers; deploy from CloudShell instead).
        if github_repo is None:
            return
        provider_arn = f"arn:aws:iam::{account}:oidc-provider/{GITHUB_OIDC_URL}"
        if not existing_oidc_provider:  # only one provider per URL per account
            provider = iam.CfnOIDCProvider(
                self, "GitHubOidcProvider",
                url=f"https://{GITHUB_OIDC_URL}",
                client_id_list=["sts.amazonaws.com"],
            )
            provider_arn = provider.attr_arn

        deploy_role = iam.Role(
            self, "GitHubDeployRole",
            role_name=DEPLOY_ROLE,
            description=f"Assumed by GitHub Actions ({github_repo}, environment {github_environment}) to run cdk deploy",
            max_session_duration=cdk.Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(provider_arn, conditions={
                "StringEquals": {
                    f"{GITHUB_OIDC_URL}:aud": "sts.amazonaws.com",
                    f"{GITHUB_OIDC_URL}:sub": f"repo:{github_repo}:environment:{github_environment}",
                },
            }),
        )
        deploy_role.add_to_policy(iam.PolicyStatement(
            sid="AssumeCdkBootstrapRoles",
            actions=["sts:AssumeRole"],
            resources=[f"arn:aws:iam::{account}:role/cdk-{CDK_QUALIFIER}-*-{account}-{region}"],
        ))

        CfnOutput(self, "DeployRoleArn", value=deploy_role.role_arn,
                  description="Set as GitHub repository variable AWS_DEPLOY_ROLE_ARN")


app = cdk.App()
github_oidc = str(app.node.try_get_context("githubOidc")).lower() != "false"
repo = app.node.try_get_context("githubRepo")
if github_oidc and (not repo or "/" not in repo):
    raise ValueError("Pass the GitHub repository (-c githubRepo=<owner>/<repo>) or disable it (-c githubOidc=false)")

FoundationStack(
    app, "EventSyncFoundation",
    github_repo=repo if github_oidc else None,
    github_environment=app.node.try_get_context("githubEnvironment") or "production",
    existing_oidc_provider=str(app.node.try_get_context("existingOidcProvider")).lower() == "true",
    env=cdk.Environment(region="ap-southeast-2"),
    # Deploy with the caller's credentials: this stack must exist before `cdk bootstrap`.
    synthesizer=cdk.LegacyStackSynthesizer(),  # template is deployed inline (<50 KB, no assets)
    description="Keyless deployment foundation for EventSyncStack: GitHub OIDC role, CDK execution policy, permissions boundary",
)
app.synth()
