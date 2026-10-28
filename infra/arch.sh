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
RACE_HOSTNAME=${RACE_HOSTNAME:-racedev}
USERNAME=${USERNAME:-anirudh}
SSHKEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICLHKwwMURvNRDZC6psxRQP4jAr8zSKBLoHY4Wvx0fAG anirudh@Anirudh-MacBook-Air.local'

say() { printf '\n=== %s ===\n' "$*"; }

[ -b "$DISK" ] || { echo "no such disk: $DISK" >&2; exit 1; }

# BIOS/GPT. VirtualBox's EFI implementation hangs this guest during early boot,
# so the dev VM uses legacy BIOS + GRUB. Real-hardware UEFI images are tested in
# QEMU with OVMF instead, which is where ISO boot testing belongs anyway.
say "partitioning $DISK (BIOS/GPT)"
sgdisk --zap-all "$DISK"
sgdisk -n1:0:+1M -t1:ef02 -c1:BIOSBOOT "$DISK"
sgdisk -n2:0:0   -t2:8304 -c2:ROOT "$DISK"
partprobe "$DISK"; sleep 2

ROOT=${DISK}2

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

say "pacstrap base system"
pacstrap -K /mnt \
  base linux linux-firmware btrfs-progs \
  sudo openssh networkmanager tailscale \
  python python-pip git base-devel \
  vim nano man-db jq ripgrep fd fzf htop \
  grub

genfstab -U /mnt >> /mnt/etc/fstab

say "configuring system"
DISK="$DISK" arch-chroot /mnt /bin/bash -euo pipefail <<CHROOT
ln -sf /usr/share/zoneinfo/UTC /etc/localtime
hwclock --systohc

echo "en_US.UTF-8 UTF-8" > /etc/locale.gen
locale-gen
echo "LANG=en_US.UTF-8" > /etc/locale.conf
echo "${RACE_HOSTNAME}" > /etc/hostname
cat > /etc/hosts <<HOSTS
127.0.0.1   localhost
::1         localhost
127.0.1.1   ${RACE_HOSTNAME}.localdomain ${RACE_HOSTNAME}
HOSTS

mkinitcpio -P

grub-install --target=i386-pc ${DISK}
sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=1/' /etc/default/grub
grub-mkconfig -o /boot/grub/grub.cfg

# Locked passwords: nothing crackable ships in a public repo.
passwd -l root
useradd -m -G wheel -s /bin/bash ${USERNAME}
passwd -l ${USERNAME}
# 00- prefix matters: sudo applies the LAST matching rule, and sudoers.d is read
# in lexical order. A general "%wheel ALL" placed after a NOPASSWD rule silently
# overrides it, which also trips pam_faillock once automation starts failing.
echo "%wheel ALL=(ALL:ALL) ALL" > /etc/sudoers.d/00-wheel
chmod 0440 /etc/sudoers.d/00-wheel

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
