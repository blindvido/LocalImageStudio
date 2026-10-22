import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from datetime import timezone
from novelai_limits import NAIGenerationLimiter


class LimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_at_least_25_seconds_and_persists_across_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [datetime(2026, 10, 13, 10, tzinfo=timezone(timedelta(hours=8)))]; waits = []
            async def sleep(seconds): waits.append(seconds); now[0] += timedelta(seconds=seconds)
            path = folder + '/usage.json'; limiter = NAIGenerationLimiter(path, now=lambda: now[0], sleep=sleep)
            first = await limiter.reserve(); second = await NAIGenerationLimiter(path, now=lambda: now[0], sleep=sleep).reserve()
            self.assertEqual((first['count'], second['count']), (1, 2))
            self.assertGreaterEqual(sum(waits), 25)
            self.assertEqual(second['minimum_interval_seconds'], 25)

    async def test_waits_from_previous_image_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            now=[datetime(2026,10,13,10,tzinfo=timezone(timedelta(hours=8)))];waits=[]
            async def sleep(seconds):waits.append(seconds);now[0]+=timedelta(seconds=seconds)
            limiter=NAIGenerationLimiter(folder+'/usage.json',now=lambda:now[0],sleep=sleep)
            await limiter.reserve();now[0]+=timedelta(seconds=8);limiter.finished()
            await limiter.reserve()
            self.assertEqual(waits,[25])

    async def test_first_start_imports_existing_paid_jobs_once(self):
        with tempfile.TemporaryDirectory() as folder:
            now=datetime(2026,10,13,10,tzinfo=timezone(timedelta(hours=8)))
            limiter=NAIGenerationLimiter(folder+'/usage.json',now=lambda:now,sleep=lambda _:asyncio.sleep(0))
            jobs={str(i):{'provider':'novelai','created_at':now.isoformat(),'updated_at':now.isoformat()} for i in range(7)}
            limiter.import_history_once(jobs);self.assertEqual((await limiter.status())['count'],7)
            limiter.import_history_once({});self.assertEqual((await limiter.status())['count'],7)

    async def test_daily_counter_is_advisory_and_does_not_block_at_four_hundred(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [datetime(2026, 10, 13, 10, tzinfo=timezone(timedelta(hours=8)))]
            async def sleep(seconds): now[0] += timedelta(seconds=seconds)
            limiter = NAIGenerationLimiter(folder + '/usage.json', now=lambda: now[0], sleep=sleep)
            limiter.path.write_text(json.dumps({'days': {'2026-10-13': 399}, 'last_started': '2026-10-13T09:00:00+08:00'}), encoding='utf-8')
            self.assertEqual((await limiter.reserve())['count'], 400)
            self.assertEqual((await limiter.reserve())['count'], 401)
            self.assertEqual((await limiter.status())['count'], 401)
            self.assertNotIn('daily_limit', await limiter.status())

    async def test_new_shanghai_date_resets_daily_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [datetime(2026, 10, 13, 23, 59, 59, tzinfo=timezone(timedelta(hours=8)))]
            async def sleep(seconds): now[0] += timedelta(seconds=seconds)
            limiter = NAIGenerationLimiter(folder + '/usage.json', now=lambda: now[0], sleep=sleep)
            await limiter.reserve();now[0] += timedelta(seconds=2)
            self.assertEqual((await limiter.reserve())['count'], 1)

    async def test_simultaneous_reservations_serialize_interval(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [datetime(2026, 10, 13, 10, tzinfo=timezone(timedelta(hours=8)))]; waits=[]
            async def sleep(seconds): waits.append(seconds);now[0]+=timedelta(seconds=seconds)
            limiter=NAIGenerationLimiter(folder+'/usage.json',now=lambda:now[0],sleep=sleep)
            results=await asyncio.gather(*(limiter.reserve() for _ in range(3)))
            self.assertEqual([x['count'] for x in results],[1,2,3])
            self.assertGreaterEqual(sum(waits),50)
