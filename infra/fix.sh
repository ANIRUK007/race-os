#!/usr/bin/env bash
# Repair pass for the Race OS dev VM, run from a booted Arch ISO:
#   RACE_PW='<password>' bash f.sh
#
# Sets the hostname correctly and gives the admin user a usable password so
# sudo works. The password is passed in at runtime and never stored here --
# this repo is public.
set -euo pipefail

DISK=${DISK:-/dev/sda}
ROOT=${DISK}2
USERNAME=${USERNAME:-anirudh}
: "${RACE_PW:?set RACE_PW=<password> before running}"

mount -o subvol=@ "$ROOT" /mnt
mount -o subvol=@home "$ROOT" /mnt/home

RACE_PW="$RACE_PW" USERNAME="$USERNAME" arch-chroot /mnt /bin/bash -euo pipefail <<'CHROOT'
echo racedev > /etc/hostname
cat > /etc/hosts <<HOSTS
127.0.0.1   localhost
::1         localhost
127.0.1.1   racedev.localdomain racedev
HOSTS
echo "${USERNAME}:${RACE_PW}" | chpasswd
usermod -U "${USERNAME}" || true
echo "password field now: $(passwd -S ${USERNAME} | awk '{print $2}')"
CHROOT

umount -R /mnt
echo "repair done, rebooting"
sleep 3
systemctl reboot
