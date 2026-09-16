"""任务阶段定义与推进。"""
from app.models import Stage

from app.workflow.stages import STAGE_ORDER

def next_stage(stage: Stage | str) -> Stage | None:
    try:
        current = Stage(stage)
        index = STAGE_ORDER.index(current)
    except ValueError:
        return None
    if index + 1 >= len(STAGE_ORDER):
        return None
    return STAGE_ORDER[index + 1]
