#!/usr/bin/env bash
# Provision the always-free Oracle VM for the public demo from your laptop,
# no console clicking. Creates (or reuses, by display-name) a VCN, internet
# gateway, route, firewall rules and the instance, then lets cloud-init run
# demo/bootstrap.sh on first boot — the demo comes up on <ip>.sslip.io with
# no SSH step at all.
#
# One-time prerequisites:
#
#   brew install oci-cli                      # or: pip install oci-cli
#   oci session authenticate                  # browser login, no API key upload
#   export OCI_CLI_AUTH=security_token        # tell the CLI to use that session
#   demo/oracle-provision.sh
#
# Session tokens last an hour; if capacity retries run long, `oci session
# refresh` in another terminal, or set up permanent keys with
# `oci setup config` instead and drop the export.
#
# Environment overrides:
#   OCI_CLI_PROFILE     profile name (default DEFAULT)
#   COMPARTMENT_OCID    defaults to the tenancy root from ~/.oci/config
#   SSH_PUBKEY          defaults to ~/.ssh/id_rsa.pub
#   MAX_ATTEMPTS        capacity-retry rounds across all ADs (default 60,
#                       one round a minute — hours-long waits are normal
#                       for A1 capacity; Ctrl-C and re-run resumes safely)
#
# Free-tier shape: A1.Flex at 2 OCPU / 12 GB — the whole always-free
# allowance since Oracle halved it in June 2026. Everything created here is
# Always Free-eligible; nothing can bill the card.
#
# Teardown: oci compute instance terminate --instance-id <ocid>
set -euo pipefail

NAME=jobd-demo
SSH_PUBKEY="${SSH_PUBKEY:-$HOME/.ssh/id_rsa.pub}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-60}"
[[ -r "$SSH_PUBKEY" ]] || { echo "no ssh public key at $SSH_PUBKEY" >&2; exit 1; }
command -v oci >/dev/null || { echo "oci CLI not installed (brew install oci-cli)" >&2; exit 1; }

profile="${OCI_CLI_PROFILE:-DEFAULT}"
COMP="${COMPARTMENT_OCID:-$(awk -v p="[$profile]" \
  '$0==p{f=1;next} /^\[/{f=0} f&&sub(/^tenancy[ =]+/,""){print;exit}' ~/.oci/config)}"
[[ -n "$COMP" ]] || { echo "could not find tenancy for profile $profile in ~/.oci/config" >&2; exit 1; }

ocid() { "$@" --query 'data[0].id' --raw-output 2>/dev/null | grep '^ocid' || true; }

# --- Network (lookup-or-create, so re-runs are safe) -------------------------
vcn=$(ocid oci network vcn list -c "$COMP" --display-name "$NAME")
[[ -n "$vcn" ]] || vcn=$(oci network vcn create -c "$COMP" --display-name "$NAME" \
  --cidr-block 10.0.0.0/16 --wait-for-state AVAILABLE --query data.id --raw-output)
echo "vcn:      $vcn"

ig=$(ocid oci network internet-gateway list -c "$COMP" --vcn-id "$vcn")
[[ -n "$ig" ]] || ig=$(oci network internet-gateway create -c "$COMP" --vcn-id "$vcn" \
  --display-name "$NAME" --is-enabled true --wait-for-state AVAILABLE \
  --query data.id --raw-output)
echo "gateway:  $ig"

rt=$(oci network vcn get --vcn-id "$vcn" --query 'data."default-route-table-id"' --raw-output)
oci network route-table update --rt-id "$rt" --force --route-rules \
  '[{"destination":"0.0.0.0/0","networkEntityId":"'"$ig"'"}]' >/dev/null
echo "route:    0.0.0.0/0 -> gateway"

sl=$(oci network vcn get --vcn-id "$vcn" --query 'data."default-security-list-id"' --raw-output)
oci network security-list update --security-list-id "$sl" --force \
  --egress-security-rules '[{"destination":"0.0.0.0/0","protocol":"all"}]' \
  --ingress-security-rules '[
    {"source":"0.0.0.0/0","protocol":"6","tcpOptions":{"destinationPortRange":{"min":22,"max":22}}},
    {"source":"0.0.0.0/0","protocol":"6","tcpOptions":{"destinationPortRange":{"min":80,"max":80}}},
    {"source":"0.0.0.0/0","protocol":"6","tcpOptions":{"destinationPortRange":{"min":443,"max":443}}}
  ]' >/dev/null
echo "firewall: 22, 80, 443 open"

subnet=$(ocid oci network subnet list -c "$COMP" --vcn-id "$vcn")
[[ -n "$subnet" ]] || subnet=$(oci network subnet create -c "$COMP" --vcn-id "$vcn" \
  --display-name "$NAME" --cidr-block 10.0.0.0/24 --wait-for-state AVAILABLE \
  --query data.id --raw-output)
echo "subnet:   $subnet"

# --- First boot: the VM sets itself up -----------------------------------
# get.docker.com runs before bootstrap so the script's docker-group
# logout/login dance never triggers; `su - ubuntu` gives a login shell where
# the fresh docker group membership is already in effect.
cloudinit=$(mktemp)
trap 'rm -f "$cloudinit"' EXIT
cat > "$cloudinit" <<'EOF'
#cloud-config
runcmd:
  # First boot can reach runcmd before DNS is ready (seen in the wild at
  # 17s after boot on Oracle) — wait for name resolution before using it.
  - timeout 300 sh -c 'until getent hosts github.com >/dev/null 2>&1; do sleep 2; done'
  - curl -fsSL https://get.docker.com | sh
  - usermod -aG docker ubuntu
  - git clone https://github.com/rsaim/jobd /home/ubuntu/jobd
  - chown -R ubuntu:ubuntu /home/ubuntu/jobd
  - su - ubuntu -c 'cd ~/jobd && demo/bootstrap.sh'
  - su - ubuntu -c 'cd ~/jobd && crontab demo/cron'
EOF

# --- Launch, retrying for capacity -------------------------------------------
image=$(oci compute image list -c "$COMP" --operating-system "Canonical Ubuntu" \
  --operating-system-version "24.04" --shape VM.Standard.A1.Flex \
  --sort-by TIMECREATED --sort-order DESC --limit 1 --query 'data[0].id' --raw-output)
echo "image:    $image (Ubuntu 24.04 aarch64)"

ads=$(oci iam availability-domain list -c "$COMP" --query 'data[].name' --raw-output |
  tr -d '[]", ' | grep .)

instance=$(ocid oci compute instance list -c "$COMP" --display-name "$NAME" \
  --lifecycle-state RUNNING)
if [[ -z "$instance" ]]; then
  for ((round = 1; round <= MAX_ATTEMPTS; round++)); do
    for ad in $ads; do
      echo "launch attempt $round in $ad ..."
      if out=$(oci compute instance launch -c "$COMP" --display-name "$NAME" \
          --availability-domain "$ad" --shape VM.Standard.A1.Flex \
          --shape-config '{"ocpus":2,"memoryInGBs":12}' \
          --image-id "$image" --subnet-id "$subnet" --assign-public-ip true \
          --ssh-authorized-keys-file "$SSH_PUBKEY" --user-data-file "$cloudinit" \
          --wait-for-state RUNNING --query data.id --raw-output 2>&1); then
        instance=$(grep '^ocid' <<< "$out" | tail -1)
        break 2
      elif grep -qi "capacity" <<< "$out"; then
        continue          # the expected free-tier error; try the next AD
      else
        echo "$out" >&2; exit 1
      fi
    done
    sleep 60
  done
  [[ -n "$instance" ]] || { echo "no A1 capacity after $MAX_ATTEMPTS rounds — re-run later, everything created so far is reused" >&2; exit 1; }
fi
echo "instance: $instance"

ip=$(oci compute instance list-vnics --instance-id "$instance" \
  --query 'data[0]."public-ip"' --raw-output)
domain="${ip//./-}.sslip.io"
echo "public ip: $ip"
echo
echo "cloud-init is now building the demo (~10 min on the free cores)."
printf "waiting for https://%s " "$domain"
for _ in $(seq 1 120); do
  if curl -fsS -o /dev/null --max-time 5 "https://$domain"; then
    echo; echo "The demo is live:  https://$domain   (user demo, password demo)"
    exit 0
  fi
  printf "."; sleep 10
done
echo
echo "not up after 20 min — check first-boot progress with:"
echo "  ssh ubuntu@$ip tail -f /var/log/cloud-init-output.log"
