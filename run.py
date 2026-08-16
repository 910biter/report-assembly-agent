"""启动入口:python run.py(监听地址/端口来自 settings,支持 IRA_HOST/IRA_PORT 与 .env)"""
import faulthandler

import uvicorn

from app.api import app
from app.config import settings

# 捕获 C 层崩溃(Python 栈):Docling/torch 等 C 库 segfault 时打印崩溃点,
# 否则进程无 traceback 直接消失,无法定位(历史上多次无痕崩溃)。
faulthandler.enable()

if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
