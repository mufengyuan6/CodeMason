"""验证确定性模块包（v1.16 落地：G11/G15）。

- phantom_edit.py：变更级验证门（SHA256 phantom-edit 检测——声称改了但 checksum 没变=拦截）
- fix_packet.py：FixPacket 机器可读失败契约（violation + verification.commands + constraints）
- fact_checker.py：事实核查子代理三态判定（VERIFIED/WRONG/UNVERIFIABLE）
- fact_preservation.py：事实保全五态校验（preserved/changed/missing/invalid/not-in-source）
- anti_spurious.py：反虚假相关（必要条件/伴随事件区分 + 扰动测试）
- lookup_before_fetch.py：lookup-before-fetch（资源取用前验证存在）
"""
