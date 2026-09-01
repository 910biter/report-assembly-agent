"""任务阶段定义与推进。"""
from app.models import Stage

_STAGE_ORDER = [
    Stage.PARSING, Stage.MATERIAL_ANALYSIS, Stage.PLANNING,
    Stage.EVIDENCE, Stage.CONFLICT, Stage.ANALYSIS, Stage.WRITING,
    Stage.REVIEW, Stage.DONE,
]


def next_stage(stage: Stage) -> Stage | None:
    try:
        index = _STAGE_ORDER.index(stage)
    except ValueError:
        return None
    if index + 1 >= len(_STAGE_ORDER):
        return None
    return _STAGE_ORDER[index + 1]
