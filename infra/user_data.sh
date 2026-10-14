#!/bin/bash
# First-boot bootstrap for the backend host (Ubuntu). The values in the
# variables below are substituted by the CDK stack at synth time.
set -euxo pipefail
exec > >(tee -a /var/log/event-sync-bootstrap.log) 2>&1

REGION="{{region}}"
SOURCE_URL="{{source_url}}"
DATA_BUCKET="{{data_bucket}}"
LOG_GROUP="{{log_group}}"
APP_DIR=/opt/event-sync

# 1 GB swap: a safety net for the 1 GiB t3.micro (image build + runtime).
if [ ! -f /swapfile ]; then
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y docker.io unzip
apt-get install -y docker-buildx || true   # BuildKit when available; legacy builder otherwise
systemctl enable --now docker

# AWS CLI v2 (not in Ubuntu's apt repos). The SSM agent is preinstalled on Ubuntu AMIs.
snap wait system seed.loaded
snap install aws-cli --classic

# Fetch the backend source uploaded by CDK and build the image on this host.
rm -rf "$APP_DIR" && mkdir -p "$APP_DIR"
/snap/bin/aws s3 cp "$SOURCE_URL" "$APP_DIR/backend.zip" --region "$REGION"
unzip -q "$APP_DIR/backend.zip" -d "$APP_DIR/backend"
docker build -t event-sync-backend "$APP_DIR/backend"

# Host networking: the container reaches the instance metadata service for the
# IAM role credentials, and serves the API directly on port 80.
docker rm -f backend 2>/dev/null || true
docker run -d --name backend --restart always --network host \
  -e DATA_SOURCE=s3 \
  -e S3_BUCKET="$DATA_BUCKET" \
  -e S3_PREFIX=data \
  -e AWS_DEFAULT_REGION="$REGION" \
  --log-driver awslogs \
  --log-opt awslogs-region="$REGION" \
  --log-opt awslogs-group="$LOG_GROUP" \
  --log-opt awslogs-stream=backend \
  event-sync-backend \
  uvicorn app.main:app --host 0.0.0.0 --port 80

# Wait for the API to come up so failures show in this log.
for i in $(seq 1 30); do
  if curl -fsS http://localhost/api/health; then echo; echo "Backend is up"; exit 0; fi
  sleep 2
done
echo "Backend did not become healthy" >&2
docker logs backend || true
exit 1
