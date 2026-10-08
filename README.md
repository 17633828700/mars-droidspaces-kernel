# mars-ci — Mi 11 Pro (mars) 的 Droidspaces 内核 CI 方案

不污染本机环境：全部编译在 GitHub Actions 上跑。

## 目标

给 Xiaomi Mi 11 Pro（`mars` / M2102K1AC）编一个带 **Droidspaces** 容器支持的内核，
同时**不破坏 vendor 模块的加载能力**（否则 Wi-Fi/相机/显示全挂 → bootloop）。

## 已实测的设备事实（本仓库方案的依据）

| 项 | 值 | 来源 |
|---|---|---|
| ROM | **crDroid v11.2**（20250309），基于 LineageOS 22，Android 15 | `ro.crdroid.build.version` |
| 内核 | **`5.4.286-qgki-g8252226d349c`** | `uname -r` |
| 编译器 | clang 19.0.0（`+pgo,+bolt,+lto,+mlgo`，r530567） | `/proc/version` |
| LTO / CFI | `CONFIG_LTO_CLANG=y`、`CONFIG_CFI_CLANG=y` | `/proc/config.gz` |
| MODVERSIONS | **`=y`** | 同上 |
| vendor 模块 vermagic | `5.4.286-qgki-g8252226d349c SMP preempt mod_unload modversions aarch64`（100 个模块去重后仅 1 条） | `/vendor/lib/modules/*.ko` |
| boot 镜像 | header **v3**；内核 raw Image @4096；ramdisk gzip @38825984 | 逐字节解析 |
| 引导器 | 已解锁（cmdline `androidboot.verifiedbootstate=orange`） | `/proc/cmdline` |
| root | Magisk `96221b69-alpha` | `/data/adb/magisk` |

## 关键设计决策（为什么与社区那套不同）

社区仓库 `birdnofoots/Mi11Pro-Droidspaces-Kernel-KSU` 是**为 MIUI 设计的**，
基于小米内部 5.4.191/233 树。它的做法里有两处被 Droidspaces 官方明确否定：

| 社区做法 | 官方立场 | 本方案 |
|---|---|---|
| 关闭 `CONFIG_MODVERSIONS` + 硬编码 vermagic 串 | 官方 CAUTION 段落：**"Do not turn off CONFIG_MODVERSIONS"** —— 结构体确实变了，模块会读错偏移 | **保持 MODVERSIONS=y**，只打 kABI 补丁 |
| 打开 `CGROUP_PIDS` / `CFS_BANDWIDTH` | 官方：这两项**破坏 kABI 且无补丁**，实测改 4101 个符号 CRC | **保持关闭**（仅失去 `--cpus` / `--pids-limit`） |

本方案改用 **LineageOS 自己的内核树** + 精确 checkout 到与设备一致的 commit。

## 目录结构

```
mars-ci/                              （总计 1.8 MB）
├── .gitattributes                 强制 LF —— 防止 CRLF 破坏补丁匹配
├── .github/workflows/build.yml    CI 主流程
├── configs/
│   └── mars-stock-from-device.config   设备原厂完整配置（6938 行，与 /proc/config.gz 零差异）
├── patches/                           Droidspaces 官方补丁（存档用，实际由脚本应用）
│   ├── 001.GKI-below-6.12-fix_sysvipc_kabi_6_7_8.patch
│   └── 002.5.10_or_lower_..._posix_mqueue.patch
├── ref/                               打包底料（从设备 boot_a.img 提取）
│   ├── boot-header.bin     4096 字节（v3 header + 零填充）
│   └── boot-ramdisk.gz     1.5 MB（原厂 ramdisk）
└── tools/
    ├── apply-kabi-patches.py    按内容精确匹配应用 kABI 补丁（不用 patch/git apply）
    ├── make-bootimg.py          把 Image 打回 boot.img（保留 AVB 尾部）
    └── verify-package.py        刷机前校验
```

## 本地已验证的环节

以下都已在本机实测通过（除实际编译，那只能在 CI 上跑）：

| 环节 | 验证方式 | 结果 |
|---|---|---|
| 源码 clone + checkout | `git clone` → fetch → checkout `8252226d349c` | ✅ 通过 |
| Makefile 版本 | 该 commit 处 `SUBLEVEL = 286` | ✅ 与设备一致 |
| kABI 补丁应用 | `apply-kabi-patches.py` 在真实树上跑 | ✅ 4 处替换全部成功 |
| boot 镜像解包 | 逐字节解析原厂 `boot_a.img` | ✅ 内核@4096、ramdisk@38825984 |
| 打包往返 | 抽出原厂内核 → 重新打包 | ✅ 偏移与大小完全一致 |
| 校验工具 | 对原厂镜像跑 `verify-package.py` | ✅ 准确报出「vermagic 匹配、Droidspaces 项缺失」 |
| LF 保证 | `.gitattributes` | ✅ 已添加（本地 core.autocrlf=true 会破坏补丁匹配） |

**未验证**：实际编译与设备启动。这必须由 CI + 真机完成。

## kABI 补丁为什么不用 `patch -p1`

上游补丁的行号基于**另一棵树**：

| 补丁声称 | 本树实际 |
|---|---|
| `@@ -1074,8 +1074,8 @@` | sched.h 的 SYSVIPC 段在 **1079** |
| `@@ -44,9 +44,15 @@` | user.h 的 ratelimit 段在 **41** |

实测：
- `git apply` **不容忍行号偏移** → 失败
- `patch -F5` 可以，但 CI 基础镜像不保证有 `patch` 命令
- `patch -p0` / `-C0` / `--recount` 均失败

**因此改为按内容精确字符串匹配**（`tools/apply-kabi-patches.py`）：
- 匹配数必须恰好为 1，否则**硬失败**，不做盲改
- 同时做正向断言（新内容在）与反向断言（旧位置已注释）

已在真树实测：4 处替换全部成功，且结构体布局保持不变。

另一个坑：补丁 002 假设 `user.h` 有 `ANDROID_OEM_DATA_ARRAY(1, 2)`，
但 LineageOS@`8252226d349c` 的实际内容只有 `ANDROID_KABI_RESERVE(1)/(2)`。
本方案已按实际内容适配。

## 使用步骤

1. **Fork LineageOS 内核仓库**（或直接用，CI 会 clone）
   `https://github.com/LineageOS/android_kernel_xiaomi_sm8350`

2. **新建自己的仓库**，把 `mars-ci/` 下所有内容推上去

3. **跑 workflow**（Actions → Build mars kernel → Run workflow）

   建议顺序：
   - 第 1 次：`variant=baseline`, `lto=as-is`
     **目的**：验证「这棵树 + 这套工具链能否产出可启动内核」。
     若 baseline 都不能启动，说明问题在树/工具链，不是 Droidspaces 配置。
   - 第 2 次：`variant=baseline`, `lto=off`
     若第 1 次因 LTO 失败（很常见），退到关 LTO 再试。
   - 第 3 次：`variant=ds`, `lto=<上一步成功的那档>`
     **这才是真正要的产物。**

4. **下载 artifact** 里的 `boot-ds.img`

5. **校验**（本地，见下）

6. **临时启动验证**（不写盘）：
   ```bash
   adb reboot bootloader
   fastboot boot boot-ds.img
   ```
   ⚠️ `fastboot boot` 后不要敲 `fastboot reboot` —— 手机会自己启动。
   若不启动，直接长按电源键强制重启即回到原系统，**盘上数据未变**。

7. **验证通过后**再永久刷入：
   ```bash
   fastboot flash boot boot-ds.img
   ```

## 本地校验（刷之前必做）

```bash
# 1) 内核 release 串必须等于设备
python3 - <<'PY'
import struct, subprocess, sys
b = open('boot-ds.img','rb').read()
assert b[:8] == b'ANDROID!', '不是 Android boot image'
ks, rs = struct.unpack_from('<II', b, 8)
print('kernel_size', ks, 'ramdisk_size', rs)
kb = 4096
assert b[kb+56:kb+60] == b'ARMd', '内核魔数不对'
print('image_size', struct.unpack_from('<Q', b, kb+16)[0])
# 抽出内核找版本串
kern = b[kb:kb+ks]
import re
vers = set(re.findall(rb'5\.4\.\d+-qgki[\w-]*', kern))
print('版本串:', [v.decode() for v in vers][:5])
assert any(b'5.4.286-qgki-g8252226d349c' in v for v in vers), \
    'vermagic 不匹配 → 100 个 vendor 模块会被拒收 → 极可能 bootloop'
print('✅ vermagic 匹配设备')
PY

# 2) 确认危险项没被打开（需要拿到 CI 的 config artifact）
grep -E '^CONFIG_(CFS_BANDWIDTH|CGROUP_PIDS)=y' config.txt && \
  echo '❌ 危险：这些项会破坏 kABI' || echo '✅ 未打开危险项'
grep -q '^CONFIG_MODVERSIONS=y' config.txt && echo '✅ MODVERSIONS 保持开启'

# 3) 确认必需项都在
for k in SYSVIPC POSIX_MQUEUE IPC_NS PID_NS DEVTMPFS; do
  grep -q "^CONFIG_${k}=y" config.txt && echo "✅ $k" || echo "❌ $k 缺失"
done
```

## 备份（已完成）

| 分区 | MD5 |
|---|---|
| `boot_a` | `5da91c34cf9259e5feb2a33135864c19` |
| `dtbo_a` | `f8cc5764a9a8f3ece34cff5dd26d2cfb` |
| `vendor_boot_a` | `628c4d765217430ccf00c0227a8e7f6e` |

设备上位于 `/sdcard/mars-backup/`。

**回退**：
```bash
fastboot flash boot boot_a.img
```

## 风险与未验证项

| 风险 | 级别 | 说明 |
|---|---|---|
| LTO/CFI 复现失败 | 🟠 高 | 设备用 clang 19 + full LTO + CFI，CI 工具链不同；所以提供 `lto=off` 退路 |
| kABI 补丁不适配 | 🟡 中 | 补丁 002 的 `user.h` 上下文与 LineageOS 实际不同，workflow 里有手工适配步骤 |
| 首次启动失败 | 🟡 中 | 用 `fastboot boot` 不写盘验证，失败无损失 |
| Droidspaces 未验证于本机 | 🟡 中 | 即使内核起来，Droidspaces App 能否正常工作需实测 |

**未验证**：本方案未在任何 mars 设备上实测过启动。第一步（baseline）就是为了尽早暴露这一点。

## 参考资料

- Droidspaces 内核配置指南（GKI 章节）
  https://github.com/ravindu644/Droidspaces-OSS/blob/main/Documentation/Kernel-Configuration.md
- LineageOS 内核树
  https://github.com/LineageOS/android_kernel_xiaomi_sm8350
- 设备 boot 镜像布局实测：见 `../mars-backup/boot-image-layout.md`
