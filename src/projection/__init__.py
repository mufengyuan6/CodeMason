"""投影层包（G17 落地：状态是事件的可验证投影）。

- contribution.py：AI 贡献报告（ContributionReport = f(EventLog, policy) 纯投影，G17⑧）
"""

from .contribution import ContributionReport, ContributionReporter

__all__ = ["ContributionReport", "ContributionReporter"]
