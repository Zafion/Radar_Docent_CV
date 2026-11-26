#!/usr/bin/env bash
# Operator-run only: creates a new file-backed LUKS2 filesystem, never a device.
set -euo pipefail
umask 077
root=$(pwd -P)
[[ -f "$root/requirements.txt" && -d "$root/auditoria_local" ]] || { echo 'STOP: run from project root'; exit 1; }
[[ $(id -u) -ne 0 && -t 0 && -t 1 ]] || { echo 'STOP: ordinary user and interactive terminal required'; exit 1; }
image="$root/auditoria_local/recovery_19nov_capacity.luks"
mountpoint="$root/auditoria_local/recovery_19nov_capacity"
mapper=funk_recovery_19nov_capacity
[[ ! -e "$image" && ! -L "$image" && ! -e "$mountpoint" && ! -L "$mountpoint" && ! -e "/dev/mapper/$mapper" && ! -L "/dev/mapper/$mapper" ]] || { echo 'STOP: path or mapper already exists'; exit 1; }
available=$(df -PB1 "$root/auditoria_local" | awk 'NR==2 {print $4}')
[[ $available =~ ^[0-9]+$ && $available -ge 42949672960 ]] || { echo 'STOP: at least 40 GiB host space required'; exit 1; }
for tool in sudo cryptsetup fallocate mkfs.ext4 mount findmnt; do command -v "$tool" >/dev/null || { echo "STOP: missing $tool"; exit 1; }; done
echo 'Creating a new 32 GiB file; existing volumes will not be modified.'
echo 'Enter the LUKS passphrase interactively; it will not be stored.'
set -o noclobber
: > "$image"
set +o noclobber
fallocate -l 32G "$image"
mkdir -m 0700 "$mountpoint"
sudo cryptsetup luksFormat --type luks2 "$image"
sudo cryptsetup open "$image" "$mapper"
sudo mkfs.ext4 -m 0 -L funk-recovery-19 "/dev/mapper/$mapper"
sudo mount -o nodev,nosuid,noexec "/dev/mapper/$mapper" "$mountpoint"
sudo chown "$(id -u):$(id -g)" "$mountpoint"
chmod 0700 "$mountpoint"
findmnt --target "$mountpoint"
echo "Prepared: $mountpoint"
