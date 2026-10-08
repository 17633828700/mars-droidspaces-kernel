#!/usr/bin/env python3
"""
刷机前本地校验：检查 CI 产出的 boot.img 与 config.txt 是否安全。

用法：
  python3 tools/verify-package.py <boot-ds.img> [config.txt]

校验项：
  A. boot.img 结构合法（ANDROID! 魔数、v3 header、内核 ARM64 魔数）
  B. ★ 内核 vermagic 精确等于设备期望值（决定 vendor 模块能否加载）
  C. 内核尺寸不超分区，且打包后 ramdisk 完好
  D. config.txt 里：必需项已开、危险项已关、MODVERSIONS 保持开启

退出码：0 = 全部通过；1 = 有致命问题，不要刷。
"""
import os
import re
import struct
import sys

EXPECTED_RELEASE = b'5.4.286-qgki-g8252226d349c'
PAGE = 4096
MAX_BOOT_IMAGE = 201326592  # 192 MiB 分区

# Droidspaces 官方 GKI 必需清单
REQUIRED = ['SYSVIPC', 'POSIX_MQUEUE', 'IPC_NS', 'PID_NS', 'DEVTMPFS']
# 官方明确：破坏 kABI 且无补丁
FORBIDDEN = ['CFS_BANDWIDTH', 'CGROUP_PIDS']

fatal = 0
warn = 0


def _emit(s):
    """GBK 控制台无法编码 emoji，降级为 ASCII。"""
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode('ascii', 'replace').decode('ascii'))


def ok(msg):
    _emit('  [OK]   %s' % msg)


def bad(msg):
    global fatal
    fatal += 1
    _emit('  [FAIL] %s' % msg)


def caution(msg):
    global warn
    warn += 1
    _emit('  [WARN] %s' % msg)


def check_bootimg(path):
    print('\n=== A. boot 镜像结构 ===')
    b = open(path, 'rb').read()
    print('  文件大小 %d 字节 (%.1f MiB)' % (len(b), len(b) / 1048576))

    if b[:8] != b'ANDROID!':
        bad('缺少 ANDROID! 魔数')
        return None
    ok('ANDROID! 魔数正确')

    hdr_size = struct.unpack_from('<I', b, 20)[0]
    ksize = struct.unpack_from('<I', b, 8)[0]
    rsize = struct.unpack_from('<I', b, 12)[0]
    hdr_ver = struct.unpack_from('<I', b, 40)[0] if len(b) > 44 else None
    print('  header_size=%d kernel_size=%d ramdisk_size=%d' % (hdr_size, ksize, rsize))

    kb = PAGE
    if b[kb + 56:kb + 60] != b'ARMd':
        bad('偏移 %d 处缺少 ARM64 魔数 ARMd' % kb)
        return None
    ok('内核 ARM64 魔数正确，位于偏移 %d' % kb)

    img_size = struct.unpack_from('<Q', b, kb + 16)[0]
    text_off = struct.unpack_from('<Q', b, kb + 8)[0]
    print('  image_size=%d text_offset=0x%x' % (img_size, text_off))
    if not (0 < img_size <= MAX_BOOT_IMAGE):
        caution('image_size 异常：%d' % img_size)
    if text_off != 0x80000:
        caution('text_offset=0x%x（设备原厂是 0x80000）' % text_off)
    else:
        ok('text_offset 与设备一致')

    if len(b) != MAX_BOOT_IMAGE:
        caution('文件大小 %d ≠ 分区大小 %d（通常无害）' % (len(b), MAX_BOOT_IMAGE))

    # C. ramdisk 完整性
    print('\n=== C. ramdisk ===')
    rd_off = ((kb + ksize + PAGE - 1) // PAGE) * PAGE
    # 跳过可能的零填充页
    for cand in range(rd_off, rd_off + PAGE * 4, PAGE):
        if b[cand:cand + 2] == b'\x1f\x8b':
            rd_off = cand
            break
    if b[rd_off:rd_off + 2] == b'\x1f\x8b':
        ok('ramdisk gzip 魔数正确，位于偏移 %d' % rd_off)
    else:
        bad('在偏移 %d 处找不到 gzip ramdisk' % rd_off)
    if rd_off + rsize > len(b):
        bad('ramdisk 越界')
    else:
        ok('ramdisk 未越界（%d 字节）' % rsize)

    return b[kb:kb + ksize]


def check_vermagic(kern):
    print('\n=== B. ★ vermagic（最关键）===')
    if kern is None:
        bad('内核未取到，跳过')
        return
    vers = set(re.findall(rb'5\.4\.\d+-qgki[\w-]*', kern))
    shown = [v.decode('ascii', 'replace') for v in sorted(vers)][:6]
    print('  内核内出现的版本串: %s' % (shown or '（无）'))
    hit = any(EXPECTED_RELEASE in v for v in vers)
    if hit:
        ok('vermagic 匹配设备：%s' % EXPECTED_RELEASE.decode())
        ok('→ vendor 模块（100 个）应能正常加载')
    else:
        bad('vermagic 不匹配！设备期望 %s' % EXPECTED_RELEASE.decode())
        bad('→ 全部 vendor 模块会被内核拒收 → Wi-Fi/相机/显示/数据 失效')
        bad('→ 极可能 bootloop。不要刷这个镜像。')


def check_config(path):
    print('\n=== D. 内核配置 ===')
    if not os.path.isfile(path):
        caution('未提供 config.txt，跳过（建议一并下载该 artifact）')
        return
    txt = open(path, errors='ignore').read()
    d = {}
    for line in txt.splitlines():
        line = line.strip()
        m = re.match(r'^(CONFIG_[A-Za-z0-9_]+)=(.*)$', line)
        if m:
            d[m.group(1)] = m.group(2)
            continue
        m = re.match(r'^# (CONFIG_[A-Za-z0-9_]+) is not set$', line)
        if m:
            d[m.group(1)] = 'n'

    for k in REQUIRED:
        v = d.get('CONFIG_' + k)
        if v == 'y':
            ok('%s = y' % k)
        else:
            bad('%s 不是 y（实际 %s）—— Droidspaces 无法工作' % (k, v))

    for k in FORBIDDEN:
        v = d.get('CONFIG_' + k)
        if v == 'y':
            bad('%s = y —— 官方明确此项破坏 kABI 且无补丁，会导致 bootloop' % k)
        else:
            ok('%s 未打开（正确）' % k)

    mv = d.get('CONFIG_MODVERSIONS')
    if mv == 'y':
        ok('MODVERSIONS = y（正确，官方禁止关闭）')
    else:
        bad('MODVERSIONS 被关闭 —— 官方明确禁止；结构体变了会让模块读错偏移')

    lv = d.get('CONFIG_LOCALVERSION', '')
    if 'g8252226d349c' in lv:
        ok('LOCALVERSION = %s（含设备 commit 后缀）' % lv)
    else:
        caution('LOCALVERSION = %s（期望含 -qgki-g8252226d349c）' % lv)

    if d.get('CONFIG_LOCALVERSION_AUTO') == 'y':
        caution('LOCALVERSION_AUTO 仍为 y —— 可能给 release 追加随机后缀')
    else:
        ok('LOCALVERSION_AUTO 已关（vermagic 可控）')

    lto = d.get('CONFIG_LTO_CLANG')
    cfi = d.get('CONFIG_CFI_CLANG')
    print('  LTO_CLANG=%s CFI_CLANG=%s（设备原厂两者均为 y）'
          % (lto, cfi))
    if lto != 'y' or cfi != 'y':
        caution('LTO/CFI 与设备原厂不同；不影响 vermagic 与模块加载，但可能影响性能/安全特性')


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    img = sys.argv[1]
    cfg = sys.argv[2] if len(sys.argv) > 2 else None
    if not os.path.isfile(img):
        print('找不到文件: %s' % img)
        return 2

    print('校验对象: %s' % img)
    kern = check_bootimg(img)
    check_vermagic(kern)
    check_config(cfg)

    print('\n' + '=' * 56)
    if fatal:
        print('结论：❌ 有 %d 项致命问题 —— 不要刷入' % fatal)
    elif warn:
        print('结论：⚠️  通过，但有 %d 项提示（可继续，注意观察）' % warn)
    else:
        print('结论：✅ 全部通过，可以刷入')
    print('=' * 56)
    print('\n刷入建议（先临时启动验证，不写盘）：')
    print('  adb reboot bootloader')
    print('  fastboot boot %s' % os.path.basename(img))
    print('若不启动：长按电源强制重启即回原系统，盘上未改变。')
    print('验证 OK 后再：fastboot flash boot %s' % os.path.basename(img))
    return 1 if fatal else 0


if __name__ == '__main__':
    sys.exit(main())
