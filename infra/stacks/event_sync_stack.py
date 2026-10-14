r"""Event Sync Service on AWS.

    Browser -> CloudFront --/*------> S3 web bucket (Angular build, private via OAC)
                         \--/api/*--> EC2 (Ubuntu, Docker, FastAPI on :80) --> S3 data bucket

The backend source is uploaded as a zip asset and the image is built on the
instance itself, so deploying needs no local Docker and no ECR repository.
"""

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    Fn,
    RemovalPolicy,
    Stack,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    aws_s3_assets as s3_assets,
    aws_s3_deployment as s3deploy,
    custom_resources as cr,
)
from constructs import Construct

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = ROOT / "frontend" / "dist" / "frontend" / "browser"
USER_DATA_TEMPLATE = Path(__file__).resolve().parents[1] / "user_data.sh"

# Rewrites extension-less paths (Angular routes such as /meetings/abc) to the SPA
# entry point. Done per-behaviour so API 404s are not turned into index.html.
SPA_REWRITE = """
function handler(event) {
  var request = event.request;
  if (!request.uri.includes('.')) { request.uri = '/index.html'; }
  return request;
}
"""


class EventSyncStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if not (FRONTEND_DIST / "index.html").exists():
            raise FileNotFoundError(
                f"Frontend build not found at {FRONTEND_DIST}. Run `npm ci && npx ng build` in frontend/ first."
            )

        ubuntu_release = self.node.try_get_context("ubuntuRelease") or "26.04"
        instance_type = self.node.try_get_context("instanceType") or "t3.micro"

        # --- Data: the two source files, uploaded under data/ -------------------------------
        data_bucket = s3.Bucket(
            self, "DataBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        s3deploy.BucketDeployment(
            self, "DataFiles",
            sources=[s3deploy.Source.asset(str(ROOT / "data"))],
            destination_bucket=data_bucket,
            destination_key_prefix="data",
        )

        # --- Network: one public subnet, no NAT gateway (nothing here needs one) ------------
        vpc = ec2.Vpc(
            self, "Vpc",
            max_azs=1,
            nat_gateways=0,
            subnet_configuration=[ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC)],
        )

        # Only CloudFront may reach the instance: look up the regional ID of the
        # AWS-managed "CloudFront origin-facing" prefix list.
        prefix_list = cr.AwsCustomResource(
            self, "CloudFrontPrefixList",
            on_update=cr.AwsSdkCall(
                service="EC2",
                action="describeManagedPrefixLists",
                parameters={"Filters": [{"Name": "prefix-list-name",
                                         "Values": ["com.amazonaws.global.cloudfront.origin-facing"]}]},
                physical_resource_id=cr.PhysicalResourceId.of("cloudfront-origin-facing"),
                output_paths=["PrefixLists.0.PrefixListId"],
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE),
            install_latest_aws_sdk=False,
        )
        backend_sg = ec2.SecurityGroup(
            self, "BackendSg", vpc=vpc,
            description="HTTP from CloudFront only; no SSH (use SSM Session Manager)",
            allow_all_outbound=True,
        )
        backend_sg.add_ingress_rule(
            ec2.Peer.prefix_list(prefix_list.get_response_field("PrefixLists.0.PrefixListId")),
            ec2.Port.tcp(80),
            "CloudFront origin-facing",
        )

        # --- Backend host -----------------------------------------------------------------
        log_group = logs.LogGroup(
            self, "BackendLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        backend_source = s3_assets.Asset(
            self, "BackendSource",
            path=str(ROOT / "backend"),
            exclude=[".venv", "**/__pycache__", ".pytest_cache", "tests", "*.pyc"],
        )

        role = iam.Role(
            self, "BackendRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")],
        )
        data_bucket.grant_read(role)
        backend_source.grant_read(role)
        log_group.grant_write(role)

        user_data = ec2.UserData.custom(
            USER_DATA_TEMPLATE.read_text(encoding="utf-8")
            .replace("\r\n", "\n")  # a Windows checkout/editor may add CRLF, which breaks bash
            .replace("{{region}}", self.region)
            .replace("{{source_url}}", backend_source.s3_object_url)
            .replace("{{data_bucket}}", data_bucket.bucket_name)
            .replace("{{log_group}}", log_group.log_group_name)
        )

        ami = ec2.MachineImage.from_ssm_parameter(
            f"/aws/service/canonical/ubuntu/server/{ubuntu_release}/stable/current/amd64/hvm/ebs-gp3/ami-id",
            os=ec2.OperatingSystemType.LINUX,
        )

        instance = ec2.Instance(
            self, "Backend",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType(instance_type),
            machine_image=ami,
            role=role,
            security_group=backend_sg,
            user_data=user_data,
            # New backend code => new asset URL => new user data => fresh instance built from it.
            user_data_causes_replacement=True,
            credit_specification=ec2.CpuCredits.STANDARD,  # throttle instead of surprise charges
            require_imdsv2=True,
            block_devices=[ec2.BlockDevice(
                device_name="/dev/sda1",
                volume=ec2.BlockDeviceVolume.ebs(10, volume_type=ec2.EbsDeviceVolumeType.GP3, encrypted=True),
            )],
        )

        # Stable address across stop/start and instance replacement.
        eip = ec2.CfnEIP(self, "BackendEip", instance_id=instance.instance_id)
        # CloudFront needs a DNS name: ec2-1-2-3-4.<region>.compute.amazonaws.com
        backend_dns = Fn.join("", [
            "ec2-", Fn.join("-", Fn.split(".", eip.attr_public_ip)), f".{self.region}.compute.amazonaws.com",
        ])

        # --- Frontend + CDN ---------------------------------------------------------------
        web_bucket = s3.Bucket(
            self, "WebBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        api_origin = origins.HttpOrigin(
            backend_dns,
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
            http_port=80,
            read_timeout=Duration.seconds(30),
        )
        api_behavior = cloudfront.BehaviorOptions(
            origin=api_origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        )

        distribution = cloudfront.Distribution(
            self, "Cdn",
            comment="Event Sync Service",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(web_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                function_associations=[cloudfront.FunctionAssociation(
                    function=cloudfront.Function(
                        self, "SpaRewrite", code=cloudfront.FunctionCode.from_inline(SPA_REWRITE),
                    ),
                    event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                )],
            ),
            additional_behaviors={"/api/*": api_behavior, "/docs": api_behavior, "/openapi.json": api_behavior},
        )

        s3deploy.BucketDeployment(
            self, "WebFiles",
            sources=[s3deploy.Source.asset(str(FRONTEND_DIST))],
            destination_bucket=web_bucket,
            distribution=distribution,  # invalidate the CDN cache on every deploy
            distribution_paths=["/*"],
        )

        # --- Outputs ----------------------------------------------------------------------
        CfnOutput(self, "AppUrl", value=f"https://{distribution.distribution_domain_name}")
        CfnOutput(self, "ApiHealthUrl", value=f"https://{distribution.distribution_domain_name}/api/health")
        CfnOutput(self, "InstanceId", value=instance.instance_id)
        CfnOutput(self, "ShellCommand",
                  value=f"aws ssm start-session --target {instance.instance_id} --region {self.region}")
        CfnOutput(self, "BootstrapLogCommand",
                  value="sudo tail -n 100 /var/log/event-sync-bootstrap.log   # run inside the SSM session")
        CfnOutput(self, "DataBucketName", value=data_bucket.bucket_name)
        CfnOutput(self, "BackendLogGroup", value=log_group.log_group_name)
