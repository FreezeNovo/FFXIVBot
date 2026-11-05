"""离线复审：合法自闭合图片不应丢掉后续正文。仓库根目录执行。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from ff14_core.na_events import LodestoneBodyParser

parser = LodestoneBodyParser()
parser.feed('<div class="news__detail__wrapper"><img src="fixture.png" />'
            '<h4>Event Period</h4><p>November 4, 2026 1:00 a.m. to '
            'November 5, 2026 2:00 a.m. (PST)</p></div>')
print('body contains Event Period: expected True, actual', 'Event Period' in parser.body)
print('accepted as complete nonempty wrapper:', bool(parser.body) and parser.depth == 0)
