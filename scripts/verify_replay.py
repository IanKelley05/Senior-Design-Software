"""Real browser checks for force/coverage maps and 30-second session replay."""
import asyncio
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

from verify_dashboard import run_isolated, ROOT
from playwright.async_api import async_playwright, expect
from rhit import database


async def verify(app, base):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            async def post(path, data=None):
                response = await page.request.post(base + path, data=data)
                assert response.ok, await response.text()
                return await response.json()

            async def hit(sid, force, rows, columns):
                await post(f"/api/sessions/{sid}/hits", {"event_id": str(uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(), "force_n": force, "contact": {"rows": rows, "columns": columns}})

            session = await post('/api/sessions', {"name": "Force and coverage replay"})
            sid = session['id']
            # Two light hits in one place; one hard hit somewhere else.
            await hit(sid, 100, [3, 4], [3, 4])
            await hit(sid, 200, [3, 4], [3, 4])
            await hit(sid, 800, [7, 8], [9, 10])
            await page.goto(base)
            await expect(page.locator('#hit-count')).to_have_text('3')
            await expect(page.locator('#replay-play')).to_be_disabled()
            await expect(page.locator('#vest')).to_have_attribute('data-peak-coverage', '2')
            coverage = await page.locator('#vest').evaluate('canvas => canvas.toDataURL()')
            await page.locator('#mode-force').click()
            await expect(page.locator('#vest')).to_have_attribute('data-peak-force', '800')
            force = await page.locator('#vest').evaluate('canvas => canvas.toDataURL()')
            assert force != coverage
            # Compare both actual renderer fields with the same saved records.
            detail = await (await page.request.get(base + f'/api/sessions/{sid}/hits')).json()
            fields = await page.evaluate('hits => RhitVest.fields(hits, JSON.parse(document.getElementById("vest-config").textContent))', detail['hits'])
            assert fields['counts'][2][2] == 2 and fields['counts'][6][8] == 1
            assert fields['forces'][2][2] == 200 and fields['forces'][6][8] == 800
            await page.locator('#end-session').click()
            await expect(page.locator('#replay-play')).to_be_enabled()
            await page.locator('#replay-restart').click()
            await expect(page.locator('#vest')).to_have_attribute('data-spatial-hits', '0')
            await expect(page.locator('#vest')).to_have_attribute('data-scale-maximum', '800')
            await page.locator('#replay-next').click()
            await expect(page.locator('#vest')).to_have_attribute('data-peak-force', '100')
            await page.locator('#replay-next').click()
            await expect(page.locator('#vest')).to_have_attribute('data-peak-force', '200')
            await page.locator('#replay-next').click()
            await expect(page.locator('#vest')).to_have_attribute('data-peak-force', '800')
            await page.locator('#replay-back').click()
            await expect(page.locator('#vest')).to_have_attribute('data-peak-force', '200')
            await expect(page.locator('#hit-rows tr.replay-current')).to_have_count(1)
            await page.locator('#mode-coverage').click()
            await expect(page.locator('#replay-progress')).to_have_attribute('data-position', '2')
            await expect(page.locator('#vest')).to_have_attribute('data-peak-coverage', '2')
            await asyncio.sleep(.7)  # Several HTTP polls must not reset this frame.
            await expect(page.locator('#replay-progress')).to_have_attribute('data-position', '2')
            await expect(page.locator('#hit-count')).to_have_text('3')

            await page.locator('#replay-restart').click()
            await page.locator('#replay-play').click()
            await expect(page.locator('#replay-progress')).to_have_attribute('data-playing', 'true')
            await page.locator('#mode-force').click()
            await expect(page.locator('#replay-progress')).to_have_attribute('data-playing', 'true')
            await page.locator('#replay-play').click()
            elapsed = await page.locator('#replay-progress').get_attribute('data-elapsed')
            await asyncio.sleep(.5)
            assert await page.locator('#replay-progress').get_attribute('data-elapsed') == elapsed

            # Measure a real uninterrupted 30-second playback (no accelerated clock).
            await page.locator('#replay-restart').click()
            start = monotonic()
            await page.locator('#replay-play').click()
            await expect(page.locator('#replay-progress')).to_have_attribute('data-playing', 'false', timeout=33000)
            assert 29.5 <= monotonic() - start < 33
            await expect(page.locator('#replay-progress')).to_have_attribute('data-position', '3')
            await expect(page.locator('#replay-progress')).to_have_attribute('data-elapsed', '30000')
            await expect(page.locator('#vest')).to_have_attribute('data-peak-force', '800')
            await page.locator('#replay-back').click()
            await page.screenshot(path=str(ROOT / '.local/replay-verified.png'), full_page=True)

            # Switch while playing; the old timer cannot touch the new session.
            other = await post('/api/sessions', {"name": "Empty session"})
            await page.locator('#check-connection').click()
            await expect(page.locator('#saved-sessions option')).to_have_count(2)
            await page.locator('#replay-play').click()
            await page.locator('#saved-sessions').select_option(str(other['id']))
            await expect(page.locator('#replay-play')).to_be_disabled()
            await expect(page.locator('#vest')).to_have_attribute('data-spatial-hits', '0')
            await asyncio.sleep(.4)
            await expect(page.locator('#replay-progress')).to_have_attribute('data-playing', 'false')
            await page.locator('#end-session').click()
            await expect(page.locator('#replay-hint')).to_contain_text('no hits')
            await expect(page.locator('#replay-play')).to_be_disabled()
            with database.connect(app.config) as conn:
                assert conn.execute('SELECT count(*) AS n FROM hits WHERE session_id=%s', (sid,)).fetchone()['n'] == 3
            assert not errors, errors
            print('PASS: force vs coverage, fixed scales, paused steps/backtracking, mode switching during replay, polling/session isolation, and actual 30-second playback.')
            print('Replay changed no saved records. Screenshot: .local/replay-verified.png')
        finally:
            await browser.close()


if __name__ == '__main__':
    run_isolated(verify)
