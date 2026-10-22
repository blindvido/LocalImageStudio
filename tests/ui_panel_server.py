"""Temporary-data UI fixture, no configured credentials and no image submission."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio_server import create_app
from aiohttp import web

if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as folder:
        app=create_app(folder)
        async def seed_job(request):
            from studio_server import STATE
            from datetime import datetime
            record=await request.json();job=record['job'];request.app[STATE].persist_job(job)
            return web.json_response({'id':job['id']})
        app.router.add_post('/api/test/seed-job',seed_job)
        web.run_app(app, host='127.0.0.1', port=8191, access_log=None)
