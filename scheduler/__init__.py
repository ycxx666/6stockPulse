"""
scheduler/__init__.py — 懒加载以避免循环导入
  application.realtime.service → scheduler.task_registry → scheduler.__init__
  如果 __init__ 立即 import .jobs，jobs 又 import application.realtime.service → 死循环
"""
def __getattr__(name):
    if name == "scheduler":
        from .jobs import scheduler as _s
        return _s
    if name == "register_all":
        from .jobs import register_all as _r
        return _r
    raise AttributeError(f"module 'scheduler' has no attribute '{name}'")
