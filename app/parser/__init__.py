"""兼容层:统一解析入口(新实现在 app/parsing/)。"""
from app.parsing import (
    PARSER_VERSION,
    parse_file,
    parse_file_with_profile,
)

__all__ = ["PARSER_VERSION", "parse_file", "parse_file_with_profile"]
