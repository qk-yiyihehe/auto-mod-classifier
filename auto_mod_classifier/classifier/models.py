from dataclasses import dataclass
from typing import Optional

from ..shared import Classification


@dataclass(frozen=True)
class ClassificationOptions:
    """单次筛选时的开关配置。"""

    use_mcmod: bool
    use_curseforge: bool = False


@dataclass(frozen=True)
class RemoteResolutionResult:
    """记录远程来源的返回结果，供回退决策统一处理。"""

    source_name: str
    preserve_unknown: bool
    classification: Optional[Classification]
