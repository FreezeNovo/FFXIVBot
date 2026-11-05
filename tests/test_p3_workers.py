import asyncio
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from ff14_core.storage import StateStore
from ff14_core.sources import OfficialSourceService, SourceFetchResult
from ff14_app.workers import ApplicationWorkers
from ff14_web.app import create_app
from ff14_web.p3 import SettingsUpdate


class WorkerTests(unittest.TestCase):
    def test_ui_to_persistent_cadence_restart_overlap_and_settings_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            store=StateStore(Path(directory)/'test.sqlite3');store.initialize()
            routes={(r.path,next(iter(r.methods))):r.endpoint for r in create_app(store).routes if hasattr(r,'path')}
            self.assertEqual(routes[('/api/runtime-settings','GET')]()['discovery_minutes'],15)
            calls=[]
            class Source:
                source_id='fixture';display_name='fixture'
                def fetch(self,transport):
                    calls.append(1)
                    return SourceFetchResult('fixture','fixture',200,b'fixture',())
            app=SimpleNamespace(store=store,source_service=OfficialSourceService(store,adapters=(Source(),)))
            now=datetime(2026,11,4,tzinfo=timezone.utc)
            async def scenario():
                worker=ApplicationWorkers(app,None)
                await asyncio.gather(worker.discover(now=now),worker.discover(now=now))
                await worker.discover(now=now+timedelta(minutes=14));self.assertEqual(len(calls),1)
                routes[('/api/runtime-settings','PUT')](SettingsUpdate(discovery_minutes=2))
                await ApplicationWorkers(app,None).discover(now=now+timedelta(minutes=14));self.assertEqual(len(calls),2)
                await worker.discover(now=now+timedelta(minutes=15));self.assertEqual(len(calls),2)
                self.assertIn('ok',routes[('/api/runtime-settings','GET')]()['probes']['discovery']['result'])
            asyncio.run(scenario())
            self.assertEqual(StateStore(store._database_path).get_runtime_settings().discovery_minutes,2)
            for data in ({'discovery_minutes':0},{'warning_hours':72,'stop_hours':48}):
                with self.assertRaises(Exception):routes[('/api/runtime-settings','PUT')](SettingsUpdate(**data))
            for value in (1.5,'2',True):
                with self.assertRaises(Exception):SettingsUpdate(discovery_minutes=value)
