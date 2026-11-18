"""Real CLI -> UDP -> PostgreSQL -> browser verification; no demo data changed."""

import asyncio
import os
from pathlib import Path
import sys
from time import monotonic

from verify_dashboard import run_isolated
from playwright.async_api import async_playwright, expect
from rhit import database

ROOT = Path(__file__).resolve().parent.parent


async def verify(app, base):
    receiver = app.extensions["udp_receiver"]
    port = receiver.port
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1366, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            async def cli(*arguments):
                process = await asyncio.create_subprocess_exec(sys.executable, "simulator.py", *arguments, "--port", str(port), cwd=ROOT,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
                assert process.returncode == 0, (stdout + stderr).decode()

            async def counts(**expected):
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    response = await page.request.get(base + "/api/udp")
                    status = await response.json()
                    if status["in_flight"] == 0 and all(status["counters"][key] == value for key, value in expected.items()):
                        return status["counters"]
                    await asyncio.sleep(.05)
                raise AssertionError((status, expected))

            regions = app.config["VEST_REGIONS"]
            await page.goto(base)
            await expect(page.locator("#start-session")).to_be_enabled()
            await cli("one", "--region", regions[0], "--repeat", "2")
            await counts(received=2, accepted=0, inactive_session=2)
            for kind in ("json", "utf8", "fields", "version", "force", "oversized"):
                await cli("malformed", "--kind", kind)
            await counts(received=8, invalid=6, accepted=0)

            await page.locator("#session-name").fill("UDP browser verification")
            await page.locator("#start-session").click()
            await expect(page.locator("#view-mode")).to_have_text("LIVE SESSION")
            sid = int(await page.locator("#saved-sessions").input_value())
            await cli("sequence", "--rate", "20", "--repeat", "2", "--force", "100")
            await counts(accepted=len(regions), duplicate=len(regions))
            await cli("continuous", "--count", "3", "--rate", "10", "--force", "50")
            await cli("one", "--region", regions[-1], "--force", "300", "--repeat", "3")
            accepted = len(regions) + 4
            received = 8 + 2 * len(regions) + 6
            expected = {"received": received, "accepted": accepted, "duplicate": len(regions) + 2,
                        "invalid": 6, "inactive_session": 2, "database_failures": 0, "processing_failures": 0}
            actual = await counts(**expected)
            assert actual == expected
            with database.connect(app.config) as conn:
                stats = conn.execute("SELECT count(*) AS count, avg(force_n) AS average, max(force_n) AS maximum FROM hits WHERE session_id=%s", (sid,)).fetchone()
                distribution = conn.execute("SELECT region,count(*) AS count FROM hits WHERE session_id=%s GROUP BY region", (sid,)).fetchall()
            assert stats["count"] == accepted
            await expect(page.locator("#hit-count")).to_have_text(str(stats["count"]))
            await expect(page.locator("#hit-rows tr")).to_have_count(stats["count"])
            await expect(page.locator("#maximum-force")).to_have_text("300")
            shown_average = float((await page.locator("#average-force").inner_text()).replace(",", ""))
            assert abs(shown_average - stats["average"]) < .01
            for row in distribution:
                await expect(page.locator(f'[data-count-region="{row["region"]}"]')).to_have_text(str(row["count"]))
            await expect(page.locator("#udp-accepted")).to_have_text(str(accepted))
            await expect(page.locator("#udp-invalid")).to_have_text("6")
            # A second real process must fail before it starts another receiver.
            process = await asyncio.create_subprocess_exec(sys.executable, "app.py", cwd=ROOT,
                env={**os.environ, "UDP_HOST": "127.0.0.1", "UDP_PORT": str(port), "APP_PORT": "0"},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
            assert process.returncode != 0 and b"Cannot bind UDP" in stdout + stderr

            await page.locator("#end-session").click()
            await expect(page.locator("#view-mode")).to_have_text("SAVED SESSION")
            await cli("one", "--region", regions[0])
            await counts(received=received + 1, accepted=accepted, inactive_session=3)
            await expect(page.locator("#udp-inactive_session")).to_have_text("3")
            await expect(page.locator("#hit-count")).to_have_text(str(accepted))
            await page.screenshot(path=str(ROOT / ".local" / "udp-verified.png"), full_page=True)
            assert not errors, errors
            print(f"PASS: CLI sent {received + 1} datagrams; {accepted} committed hits displayed correctly in PostgreSQL, SVG, table and statistics.")
            print(f"PASS: {len(regions) + 2} duplicates, 6 invalid packets, 3 inactive-session packets; second receiver process refused.")
            print("Screenshot: .local/udp-verified.png")
        finally:
            await browser.close()


if __name__ == "__main__":
    run_isolated(verify, with_udp=True)
