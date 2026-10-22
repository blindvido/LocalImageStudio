"""Durable local NovelAI request pacing and advisory daily usage counter."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Shanghai remains UTC+8 year-round; the Windows portable runtime need not
# ship the optional system tzdata package for this fixed civil-time boundary.
SHANGHAI = timezone(timedelta(hours=8), name='Asia/Shanghai')


class NAIGenerationLimiter:
    def __init__(self, path, *, interval_seconds=25, now=None, sleep=None):
        self.path = Path(path)
        self.interval_seconds = interval_seconds
        self.now = now or (lambda: datetime.now(SHANGHAI))
        self.sleep = sleep or asyncio.sleep
        self.lock = asyncio.Lock()

    def _load(self):
        if not self.path.exists(): return {'days': {}, 'last_started': None}
        try:
            record = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ValueError(f'NAI 限流记录读取失败，为避免超额提交已停止生图：{self.path.name}') from None
        if not isinstance(record, dict) or not isinstance(record.get('days'), dict):
            raise ValueError('NAI 限流记录格式无效，为避免超额提交已停止生图。')
        return record

    def _save(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def import_history_once(self, jobs):
        if self.path.exists(): return
        now = self.now().astimezone(SHANGHAI); today = now.date().isoformat()
        previous = []; count = 0
        for job in jobs.values():
            if job.get('provider') != 'novelai': continue
            try: created = datetime.fromisoformat(job['created_at']).astimezone(SHANGHAI)
            except (KeyError, ValueError, TypeError): continue
            if created.date().isoformat() == today:
                count += 1
                try: previous.append(datetime.fromisoformat(job['updated_at']).astimezone(SHANGHAI))
                except (KeyError, ValueError, TypeError): previous.append(created)
        last = max(previous).isoformat(timespec='seconds') if previous else None
        self._save({'days': {today: count}, 'last_started': last, 'last_finished': last})

    async def reserve(self):
        async with self.lock:
            while True:
                now = self.now().astimezone(SHANGHAI)
                record = self._load()
                day = now.date().isoformat()
                days = {k: v for k, v in record['days'].items() if k >= (now.date() - timedelta(days=31)).isoformat()}
                count = days.get(day, 0)
                if type(count) is not int or count < 0:
                    raise ValueError('NAI 当日计数记录异常，为避免超额提交已停止生图。')
                last = record.get('last_finished') or record.get('last_started')
                wait = 0
                if last:
                    try: last_at = datetime.fromisoformat(last)
                    except (ValueError, TypeError): raise ValueError('NAI 间隔记录异常，为避免密集提交已停止生图。') from None
                    wait = max(0, self.interval_seconds - (now - last_at).total_seconds())
                if wait > 0:
                    await self.sleep(wait)
                    continue
                days[day] = count + 1
                updated = {'days': days, 'last_started': now.isoformat(timespec='seconds')}
                if record.get('last_finished'): updated['last_finished'] = record['last_finished']
                self._save(updated)
                return {'date': day, 'count': count + 1,
                        'minimum_interval_seconds': self.interval_seconds}

    def finished(self):
        record = self._load()
        record['last_finished'] = self.now().astimezone(SHANGHAI).isoformat(timespec='seconds')
        self._save(record)

    async def status(self):
        now = self.now().astimezone(SHANGHAI); record = self._load()
        return {'date': now.date().isoformat(), 'count': record['days'].get(now.date().isoformat(), 0),
                'minimum_interval_seconds': self.interval_seconds}
