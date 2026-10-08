#!/usr/bin/env python3
"""
把 CI 产出的裸内核 Image 打包成可 fastboot 的 boot.img。

依据：对设备 boot_a.img 的逐字节实测（见 mars-backup/boot-image-layout.md）
  - 格式：Android boot image header v3（header_size = 1580）
  - 内核位于偏移 4096（页对齐），未压缩 raw ARM64 Image
  - ramdisk 位于 38825984，gzip 格式
  - 分区大小 201326592 字节（192 MiB）

输入是「底料」而非完整镜像，避免在仓库里放 192MB：
  ref/boot-header.bin   设备 boot.img 的前 4096 字节（含 v3 header + 零填充）
  ref/boot-ramdisk.gz   设备 ramdisk（gzip）

用法：
  python3 tools/make-bootimg.py <boot-header.bin> <boot-ramdisk.gz> <new-Image> <out-boot.img>

策略：
  - 沿用原 header，只更新 kernel_size / ramdisk_size
  - 内核区换成新 Image；ramdisk 紧跟其后（页对齐）
  - 尾部零填充补齐到分区大小
"""
import struct
import sys

PAGE = 4096
K_OFF = 4096
HDR_KERNEL_SIZE_OFF = 8
HDR_RAMDISK_SIZE_OFF = 12
HDR_HEADER_VERSION_OFF = 40
MAX_BOOT_IMAGE = 201326592  # 192 MiB，与分区一致


def main():
    if len(sys.argv) != 5:
        print(__doc__)
        return 2
    hdr_path, rd_path, img_path, out_path = sys.argv[1:5]

    header = bytearray(open(hdr_path, 'rb').read())
    if bytes(header[:8]) != b'ANDROID!':
        print('错误：header 不是 Android boot image')
        return 1

    hdr_size = struct.unpack_from('<I', header, 20)[0]
    hdr_ver = struct.unpack_from('<I', header, HDR_HEADER_VERSION_OFF)[0]
    print('header: header_size=%d  header_version=%d' % (hdr_size, hdr_ver))
    if hdr_ver != 3:
        print('警告：期望 v3，实际 v%d —— 请核对布局假设' % hdr_ver)

    ramdisk = open(rd_path, 'rb').read()
    if ramdisk[:2] != b'\x1f\x8b':
        print('错误：ramdisk 不是 gzip（head=%s）' % ramdisk[:4].hex())
        return 1
    print('ramdisk: %d 字节 (gzip)' % len(ramdisk))

    new_img = open(img_path, 'rb').read()
    if new_img[56:60] != b'ARMd':
        print('错误：新 Image 偏移 56 处缺少 ARM64 魔数（head=%s）'
              % new_img[56:60].hex())
        return 1
    text_off = struct.unpack_from('<Q', new_img, 8)[0]
    img_size = struct.unpack_from('<Q', new_img, 16)[0]
    print('new Image: %d 字节  image_size=%d  text_offset=0x%x'
          % (len(new_img), img_size, text_off))
    if text_off != 0x80000:
        print('警告：text_offset=0x%x，设备原厂为 0x80000' % text_off)

    # 组装
    out = bytearray(header[:hdr_size])
    if len(out) < K_OFF:
        out.extend(b'\x00' * (K_OFF - len(out)))
    out.extend(new_img)
    pad = (-len(out)) % PAGE
    out.extend(b'\x00' * pad)
    rd_new_off = len(out)
    out.extend(ramdisk)

    if len(out) > MAX_BOOT_IMAGE:
        print('错误：产物 %d 字节 > 分区 %d 字节' % (len(out), MAX_BOOT_IMAGE))
        return 1

    # ★ 保留 ramdisk 之后、原镜像里已有的尾部数据
    #   实测原厂 boot_a.img 在 ramdisk 结束（40443608）后还有 265 字节非零数据
    #   （首个差异字节是 'A','V','B','0' —— 疑似 AVB/校验相关元数据）。
    #   MIUI/crDroid 的 bootloader 可能校验它，因此原样搬运，不要清零。
    tail_src_start = rd_off + rsize          # 原镜像里 ramdisk 之后的起点
    tail = bytes(base[tail_src_start:tail_src_start + 65536]).rstrip(b'\x00')
    if tail:
        out.extend(tail)
        print('  保留尾部元数据 %d 字节（首个字节 0x%02x）' % (len(tail), tail[0]))
    else:
        print('  尾部无额外元数据')

    struct.pack_into('<I', out, HDR_KERNEL_SIZE_OFF, len(new_img))
    struct.pack_into('<I', out, HDR_RAMDISK_SIZE_OFF, len(ramdisk))

    # 补齐到分区大小（与原分区一致）
    if len(out) < MAX_BOOT_IMAGE:
        out.extend(b'\x00' * (MAX_BOOT_IMAGE - len(out)))

    open(out_path, 'wb').write(out)
    print('')
    print('写出 %s' % out_path)
    print('  总大小      %d' % len(out))
    print('  内核偏移    %d  长度 %d' % (K_OFF, len(new_img)))
    print('  ramdisk偏移 %d  长度 %d' % (rd_new_off, len(ramdisk)))
    print('')
    print('刷机前请先校验：python3 tools/verify-package.py %s' % out_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
