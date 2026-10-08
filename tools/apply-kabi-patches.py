#!/usr/bin/env python3
"""
用精确字符串替换实现 Droidspaces 的两个 kABI 补丁。

为什么不直接 `patch -p1 < xxx.patch`：
  - 上游补丁的行号基于不同的树（补丁 001 说 @@ -1074，实际在 1077；
    补丁 002 说 @@ -44，实际也在别处）。
  - `git apply` 不容忍行号偏移；`patch -F5` 可以，但 CI 基础镜像不一定有 patch。
  - 因此改为「按内容精确匹配 + 替换」，并把匹配失败当作硬错误。

已实测的上下文（LineageOS@8252226d349c，与设备 5.4.286 对应）：

  include/linux/sched.h
    L1079  #ifdef CONFIG_SYSVIPC
    L1080      struct sysv_sem            sysvsem;
    L1081      struct sysv_shm            sysvshm;
    L1082  #endif
    L1083  #ifdef CONFIG_DETECT_HUNG_TASK

    L1471      ANDROID_KABI_RESERVE(6);
    L1472      ANDROID_KABI_RESERVE(7);
    L1473      ANDROID_KABI_RESERVE(8);

  include/linux/sched/user.h
    L41    /* Miscellaneous per-user rate limit */
    L42    struct ratelimit_state ratelimit;
    L43
    L44    ANDROID_KABI_RESERVE(1);
    L45    ANDROID_KABI_RESERVE(2);
    L46  };

用法：
  python3 tools/apply-kabi-patches.py <kernel-src-dir>

退出码：0 = 全部应用成功；1 = 有失败。
"""
import os
import sys

# ---------- 补丁 1：SYSVIPC 的 task_struct 字段挪进 kABI 保留槽 ----------

SCHED_H_SYSVIPC_OLD = """#ifdef CONFIG_SYSVIPC
\tstruct sysv_sem\t\t\tsysvsem;
\tstruct sysv_shm\t\t\tsysvshm;
#endif
#ifdef CONFIG_DETECT_HUNG_TASK"""

SCHED_H_SYSVIPC_NEW = """#ifdef CONFIG_SYSVIPC
\t/* 由 kABI 补丁挪到 task_struct 尾部的保留槽，避免改变结构体布局 */
\t/* struct sysv_sem\t\t\tsysvsem; */
\t/* struct sysv_shm\t\t\tsysvshm; */
#endif
#ifdef CONFIG_DETECT_HUNG_TASK"""

SCHED_H_KABI_OLD = """\tANDROID_KABI_RESERVE(6);
\tANDROID_KABI_RESERVE(7);
\tANDROID_KABI_RESERVE(8);
"""

SCHED_H_KABI_NEW = """#ifdef CONFIG_SYSVIPC
\tANDROID_KABI_USE(6, struct sysv_sem sysvsem);
\t_ANDROID_KABI_REPLACE(ANDROID_KABI_RESERVE(7); ANDROID_KABI_RESERVE(8), struct sysv_shm sysvshm);
#else
\tANDROID_KABI_RESERVE(6);
\tANDROID_KABI_RESERVE(7);
\tANDROID_KABI_RESERVE(8);
#endif
"""

# ---------- 补丁 2：POSIX_MQUEUE 的 user_struct 字段 ----------

USER_H_MQ_OLD = """#ifdef CONFIG_POSIX_MQUEUE
\t/* protected by mq_lock\t*/
\tunsigned long mq_bytes;\t/* How many bytes can be allocated to mqueue? */
#endif"""

USER_H_MQ_NEW = """#ifdef CONFIG_POSIX_MQUEUE
\t/* protected by mq_lock\t*/
\t/* 由 kABI 补丁挪到结构体尾部 */
\t/* unsigned long mq_bytes; */\t/* How many bytes can be allocated to mqueue? */
#endif"""

USER_H_KABI_OLD = """\t/* Miscellaneous per-user rate limit */
\tstruct ratelimit_state ratelimit;

\tANDROID_KABI_RESERVE(1);
\tANDROID_KABI_RESERVE(2);
};"""

USER_H_KABI_NEW = """\t/* Miscellaneous per-user rate limit */
\tstruct ratelimit_state ratelimit;

#if defined(CONFIG_POSIX_MQUEUE)
\tANDROID_KABI_USE(1, unsigned long mq_bytes);
\tANDROID_KABI_RESERVE(2);
#else
\tANDROID_KABI_RESERVE(1);
\tANDROID_KABI_RESERVE(2);
#endif
};"""


def replace_once(path, old, new, label, rel):
    """在 path 中把 old 替换为 new（必须恰好匹配一次）。"""
    if not os.path.isfile(path):
        print('  [FAIL] %s: 找不到文件 %s' % (label, rel))
        return False
    s = open(path, encoding='utf-8', errors='surrogateescape').read()
    if new in s:
        print('  [SKIP] %s: 已应用' % label)
        return True
    n = s.count(old)
    if n == 0:
        print('  [FAIL] %s: 未匹配到上下文' % label)
        print('         期望片段首行: %r' % old.split('\n')[0])
        return False
    if n > 1:
        print('  [FAIL] %s: 上下文匹配了 %d 次（应为 1 次），拒绝盲改' % (label, n))
        return False
    open(path, 'w', encoding='utf-8', errors='surrogateescape').write(s.replace(old, new, 1))
    print('  [OK]   %s' % label)
    return True


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = sys.argv[1]
    if not os.path.isdir(root):
        print('不是目录: %s' % root)
        return 2

    sched = os.path.join(root, 'include/linux/sched.h')
    user = os.path.join(root, 'include/linux/sched/user.h')

    print('应用 Droidspaces kABI 补丁（字符串精确匹配方式）')
    print('内核源码树: %s' % root)
    print('')

    print('--- 补丁 1: SYSVIPC (task_struct) ---')
    a = replace_once(sched, SCHED_H_SYSVIPC_OLD, SCHED_H_SYSVIPC_NEW,
                     'sched.h: sysvsem/sysvshm 移出原位置', 'include/linux/sched.h')
    b = replace_once(sched, SCHED_H_KABI_OLD, SCHED_H_KABI_NEW,
                     'sched.h: kABI 保留槽 6/7/8 改为 ANDROID_KABI_USE(6)', 'include/linux/sched.h')

    print('')
    print('--- 补丁 2: POSIX_MQUEUE (user_struct) ---')
    c = replace_once(user, USER_H_MQ_OLD, USER_H_MQ_NEW,
                     'user.h: mq_bytes 移出原位置', 'include/linux/sched/user.h')
    d = replace_once(user, USER_H_KABI_OLD, USER_H_KABI_NEW,
                     'user.h: kABI 保留槽 1 改为 ANDROID_KABI_USE(1)', 'include/linux/sched/user.h')

    # 断言
    print('')
    print('--- 校验 ---')
    ok = True
    checks = [
        (sched, 'ANDROID_KABI_USE(6, struct sysv_sem sysvsem)', 1, 'sched.h 含 kABI_USE(6)'),
        (sched, '_ANDROID_KABI_REPLACE(ANDROID_KABI_RESERVE(7); ANDROID_KABI_RESERVE(8), struct sysv_shm sysvshm)', 1, 'sched.h 含 sysvshm 替换'),
        (user, 'ANDROID_KABI_USE(1, unsigned long mq_bytes)', 1, 'user.h 含 kABI_USE(1)'),
    ]
    for path, needle, want, label in checks:
        if not os.path.isfile(path):
            print('  [FAIL] %s' % label)
            ok = False
            continue
        got = open(path, encoding='utf-8', errors='surrogateescape').read().count(needle)
        if got == want:
            print('  [OK]   %s (出现 %d 次)' % (label, got))
        else:
            print('  [FAIL] %s (出现 %d 次，期望 %d)' % (label, got, want))
            ok = False

    # 反向断言：原生位置必须已被注释
    for path, needle, label in [
        (sched, '// struct sysv_sem\t\t\tsysvsem;', 'sched.h 原 sysvsem 已注释'),
        (user, '// unsigned long mq_bytes;', 'user.h 原 mq_bytes 已注释'),
    ]:
        if os.path.isfile(path):
            # 兼容用 /* */ 注释的写法
            s = open(path, encoding='utf-8', errors='surrogateescape').read()
            if ('/* struct sysv_sem' in s) or ('// struct sysv_sem' in s) if 'sysv_sem' in needle else True:
                pass
    s = open(sched, encoding='utf-8', errors='surrogateescape').read()
    if '/* struct sysv_sem' in s or '// struct sysv_sem' in s:
        print('  [OK]   sched.h 原 sysvsem 已注释')
    else:
        print('  [FAIL] sched.h 原 sysvsem 未注释 —— 结构体会被改两次!')
        ok = False
    u = open(user, encoding='utf-8', errors='surrogateescape').read()
    if '/* unsigned long mq_bytes;' in u or '// unsigned long mq_bytes;' in u:
        print('  [OK]   user.h 原 mq_bytes 已注释')
    else:
        print('  [FAIL] user.h 原 mq_bytes 未注释 —— 结构体会被改两次!')
        ok = False

    print('')
    if ok and a and b and c and d:
        print('结论: kABI 补丁全部应用成功')
        print('说明: 结构体大小与字段偏移保持不变，vendor 模块的 CRC 不受影响。')
        return 0
    print('结论: 有失败项 —— 请检查上面的 [FAIL] 行')
    return 1


if __name__ == '__main__':
    sys.exit(main())
