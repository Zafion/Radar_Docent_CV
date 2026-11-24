#!/usr/bin/env bash
# Interactive, operator-run preparation only. No existing devices are formatted.
set -euo pipefail
umask 077
root=$(pwd -P)
[[ -f "$root/requirements.txt" && -d "$root/auditoria_local" ]] || { echo 'STOP: run from the project root'; exit 1; }
image="$root/auditoria_local/recovery_final_capacity.luks"
mountpoint="$root/auditoria_local/recovery_final_capacity"
mapper=funk_recovery_final_capacity
[[ $(id -u) -ne 0 && -t 0 && -t 1 ]] || { echo 'STOP: ordinary user and terminal required'; exit 1; }
[[ ! -e "$image" && ! -L "$image" && ! -e "$mountpoint" && ! -L "$mountpoint" && ! -e /dev/mapper/$mapper ]] || { echo 'STOP: image, mount point or mapper already exists'; exit 1; }
available=$(df -Pk "$root/auditoria_local" | awk 'NR==2 {print $4}')
[[ $available =~ ^[0-9]+$ && $available -ge 41943040 ]] || { echo 'STOP: at least 40 GiB free required'; exit 1; }
for tool in sudo cryptsetup fallocate mkfs.ext4 mount; do command -v "$tool" >/dev/null || { echo "STOP: missing $tool"; exit 1; }; done
echo 'A new 32 GiB encrypted file will be created; existing volumes remain untouched.'
echo 'Enter the LUKS passphrase directly in the terminal; it is not stored by this script.'
set -o noclobber
: > "$image"
set +o noclobber
fallocate -l 32G "$image"
mkdir -m 0700 "$mountpoint"
sudo cryptsetup luksFormat --type luks2 "$image"
sudo cryptsetup open "$image" "$mapper"
sudo mkfs.ext4 -m 0 -L funk-recovery-final "/dev/mapper/$mapper"
sudo mount -o nodev,nosuid,noexec "/dev/mapper/$mapper" "$mountpoint"
sudo chown "$(id -u):$(id -g)" "$mountpoint"
chmod 0700 "$mountpoint"
echo "Prepared: $mountpoint"
