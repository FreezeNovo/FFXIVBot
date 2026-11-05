"""P3安全文件日志：标准库轮转，初始化失败即明确报错。"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from ff14_core.diagnostics import safe_reason


class SafeFormatter(logging.Formatter):
    def format(self,record):
        import copy
        safe=copy.copy(record)
        if safe.name!='ffxivbot':
            safe.msg=safe_reason(safe.getMessage());safe.args=()
        return super().format(safe)

    def formatException(self,exc_info):
        return '\n'.join(safe_reason(line) for line in super().formatException(exc_info).splitlines())


def configure_logging(directory: Path,*,max_bytes=2*1024*1024,backup_count=3):
    directory.mkdir(parents=True,exist_ok=True)
    logger=logging.getLogger('ffxivbot')
    if any(getattr(h,'baseFilename',None)==str((directory/'p3.log').resolve()) for h in logger.handlers):return logger
    formatter=SafeFormatter('%(asctime)sZ %(levelname)s %(message)s')
    import time
    formatter.converter=time.gmtime
    file=RotatingFileHandler(directory/'p3.log',maxBytes=max_bytes,backupCount=backup_count,encoding='utf-8')
    console=logging.StreamHandler()
    for handler in (file,console):handler.setFormatter(formatter);logger.addHandler(handler)
    logger.setLevel(logging.INFO);logger.propagate=False
    return logger
