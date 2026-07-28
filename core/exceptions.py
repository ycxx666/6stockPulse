"""
core/exceptions.py — 项目自定义异常
"""

class StockPulseError(Exception):
    """基础异常"""

class StockNotFoundError(StockPulseError):
    """股票代码不存在"""

class CrawlerError(StockPulseError):
    """爬虫异常"""

class CrawlerRateLimitError(CrawlerError):
    """爬虫被限流 (429)"""

class CrawlerParseError(CrawlerError):
    """数据解析失败"""

class ConfigError(StockPulseError):
    """配置错误"""

class DatabaseError(StockPulseError):
    """数据库异常"""
