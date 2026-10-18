#!/usr/bin/env bash
# Unattended Arch install for the Race OS build/test VM.
#
# Run from a booted Arch ISO:
#   curl -sL https://raw.githubusercontent.com/ANIRUK007/race-os/setup/infra/arch.sh | bash
#
# DESTROYS everything on $DISK. Intended for a fresh VM only.
#
# No secrets live in this file -- the repo is public. The user is created with a
# LOCKED password and an SSH public key; set a console password after first login.
set -euo pipefail

DISK=${DISK:-/dev/sda}
HOSTNAME=${HOSTNAME:-racedev}
USERNAME=${USERNAME:-anirudh}
SSHKEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEqa1ffNIVznHRyzfoVegmDmSdp9t8DsH9L4lfyUPa5G tericsoft@Tericsofts-MacBook-Air.local'

say() { printf '\n=== %s ===\n' "$*"; }

[ -b "$DISK" ] || { echo "no such disk: $DISK" >&2; exit 1; }
[ -d /sys/firmware/efi ] || { echo "not booted in UEFI mode" >&2; exit 1; }

say "partitioning $DISK"
sgdisk --zap-all "$DISK"
sgdisk -n1:0:+1G -t1:ef00 -c1:EFI "$DISK"
sgdisk -n2:0:0   -t2:8304 -c2:ROOT "$DISK"
partprobe "$DISK"; sleep 2

ESP=${DISK}1
ROOT=${DISK}2

mkfs.fat -F32 -n EFI "$ESP"
mkfs.btrfs -f -L RACEDEV "$ROOT"

# Subvolume layout matches the snapper convention so snapshots/rollback work later.
say "creating btrfs subvolumes"
mount "$ROOT" /mnt
for sv in @ @home @log @pkg @snapshots; do btrfs subvolume create "/mnt/$sv"; done
umount /mnt

OPTS="noatime,compress=zstd:1,ssd,space_cache=v2"
mount -o "$OPTS,subvol=@" "$ROOT" /mnt
mkdir -p /mnt/{home,var/log,var/cache/pacman/pkg,.snapshots,boot}
mount -o "$OPTS,subvol=@home"      "$ROOT" /mnt/home
mount -o "$OPTS,subvol=@log"       "$ROOT" /mnt/var/log
mount -o "$OPTS,subvol=@pkg"       "$ROOT" /mnt/var/cache/pacman/pkg
mount -o "$OPTS,subvol=@snapshots" "$ROOT" /mnt/.snapshots
mount "$ESP" /mnt/boot

say "pacstrap base system"
pacstrap -K /mnt \
  base linux linux-firmware btrfs-progs \
  sudo openssh networkmanager tailscale \
  python python-pip git base-devel \
  vim nano man-db jq ripgrep fd fzf htop \
  efibootmgr

genfstab -U /mnt >> /mnt/etc/fstab

say "configuring system"
ROOT_UUID=$(blkid -s UUID -o value "$ROOT")

arch-chroot /mnt /bin/bash -euo pipefail <<CHROOT
ln -sf /usr/share/zoneinfo/UTC /etc/localtime
hwclock --systohc

echo "en_US.UTF-8 UTF-8" > /etc/locale.gen
locale-gen
echo "LANG=en_US.UTF-8" > /etc/locale.conf
echo "${HOSTNAME}" > /etc/hostname
cat > /etc/hosts <<HOSTS
127.0.0.1   localhost
::1         localhost
127.0.1.1   ${HOSTNAME}.localdomain ${HOSTNAME}
HOSTS

mkinitcpio -P

# systemd-boot: ESP is mounted at /boot so kernels land on it directly.
bootctl install
cat > /boot/loader/loader.conf <<LOADER
default arch.conf
timeout 1
console-mode max
editor no
LOADER
cat > /boot/loader/entries/arch.conf <<ENTRY
title   Race OS dev (Arch)
linux   /vmlinuz-linux
initrd  /initramfs-linux.img
options root=UUID=${ROOT_UUID} rootflags=subvol=@ rw
ENTRY

# Locked passwords: nothing crackable ships in a public repo.
passwd -l root
useradd -m -G wheel -s /bin/bash ${USERNAME}
passwd -l ${USERNAME}
echo "%wheel ALL=(ALL:ALL) ALL" > /etc/sudoers.d/wheel
chmod 0440 /etc/sudoers.d/wheel

install -d -m 700 -o ${USERNAME} -g ${USERNAME} /home/${USERNAME}/.ssh
echo '${SSHKEY}' > /home/${USERNAME}/.ssh/authorized_keys
chmod 600 /home/${USERNAME}/.ssh/authorized_keys
chown ${USERNAME}:${USERNAME} /home/${USERNAME}/.ssh/authorized_keys

cat > /etc/ssh/sshd_config.d/10-raceos.conf <<SSHD
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
SSHD

systemctl enable sshd NetworkManager tailscaled
CHROOT

say "done -- unmounting and rebooting"
umount -R /mnt
echo "Install complete. Rebooting in 5s."
sleep 5
systemctl reboot
