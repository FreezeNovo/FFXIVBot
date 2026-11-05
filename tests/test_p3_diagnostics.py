import logging
from pathlib import Path
import tempfile
import unittest
from ff14_app.logging_setup import configure_logging
from ff14_core.diagnostics import safe_reason,diagnostic


class DiagnosticTests(unittest.TestCase):
    def test_sensitive_markers_filtered_and_rotation_bounded(self):
        raw="TimeoutError ws://private.invalid:1234/connect?token=MARK_URI access_token=MARK_TOKEN cookie=MARK_COOKIE {'secret': 'MARK_SECRET', 'group_id': '123456789'}"
        result=safe_reason(raw)
        for marker in ('MARK_URI','MARK_TOKEN','MARK_COOKIE','MARK_SECRET','123456789','private.invalid'):self.assertNotIn(marker,result)
        with tempfile.TemporaryDirectory() as directory:
            logger=configure_logging(Path(directory),max_bytes=700,backup_count=2)
            try:
                diagnostic(stage='fixture',source_id='china_news_api',document_id='fixture-id',target_id='internal-uuid',action='preserve',error=raw)
                child=logging.getLogger('ffxivbot.runtime')
                try:raise TimeoutError(raw)
                except TimeoutError:child.exception('fixture outer failure %s',raw)
                for index in range(30):diagnostic(stage='fixture',source_id='fixture',document_id=str(index),action='rotate',error=raw)
                files=list(Path(directory).glob('p3.log*'));self.assertLessEqual(len(files),3)
                for path in files:
                    self.assertLessEqual(path.stat().st_size,1000)
                    text=path.read_text()
                    self.assertIn('stage=fixture',text)
                    for marker in ('MARK_URI','MARK_TOKEN','MARK_COOKIE','MARK_SECRET','123456789'):self.assertNotIn(marker,text)
            finally:
                for handler in tuple(logger.handlers):handler.close();logger.removeHandler(handler)
