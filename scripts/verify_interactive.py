"""Two actual websites: click sender -> UDP receiver -> DB -> live heatmap."""
import asyncio
from threading import Thread

from verify_dashboard import run_isolated, ROOT, QuietHandler
from playwright.async_api import async_playwright, expect
from werkzeug.serving import make_server
from simulator_web import create_simulator
from rhit import database


async def verify(app, base):
    simulator = create_simulator({"TESTING": True, "MAIN_APP_URL": base, "UDP_PORT": app.extensions["udp_receiver"].port})
    server = make_server("127.0.0.1", 0, simulator, threaded=True, request_handler=QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="msedge", headless=True)
            try:
                context = await browser.new_context(viewport={"width": 1440, "height": 1000})
                main = await context.new_page()
                sender = await context.new_page()
                errors = []
                for page in (main, sender):
                    page.on("pageerror", lambda error: errors.append(str(error)))
                await main.goto(base)
                await sender.goto(f"http://127.0.0.1:{server.server_port}")
                await expect(sender.locator("#target-vest")).to_have_attribute("aria-disabled", "true")
                await main.bring_to_front()
                await main.locator("#session-name").fill("Interactive grid verification")
                await main.locator("#start-session").click()
                await expect(main.locator("#view-mode")).to_have_text("LIVE SESSION")
                sid = int(await main.locator("#saved-sessions").input_value())
                await sender.bring_to_front()
                await expect(sender.locator("#target-vest")).to_have_attribute("aria-disabled", "false")

                async def click(x, y):
                    box = await sender.locator("#target-vest").bounding_box()
                    await sender.locator("#target-vest").click(position={"x": x/360*box["width"], "y": y/500*box["height"]})
                    await expect(sender.locator("#send-message")).to_contain_text("Confirmed in session")
                    await expect(sender.locator("#repeat-hit")).to_be_enabled()

                # Head is not a contact area and must not send a datagram.
                box = await sender.locator("#target-vest").bounding_box()
                await sender.locator("#target-vest").click(position={"x": box["width"]/2, "y": 42/500*box["height"]})
                await asyncio.sleep(.2)
                assert app.extensions["udp_receiver"].snapshot()["counters"]["received"] == 0
                await sender.locator('[data-force="100"]').click()
                await sender.locator('[data-size="small"]').click()
                await click(115, 180)
                await sender.locator("#repeat-hit").click()
                await expect(sender.locator("#repeat-hit")).to_be_enabled()
                await sender.locator('[data-force="600"]').click()
                await sender.locator('[data-size="large"]').click()
                await click(230, 340)

                with database.connect(app.config) as conn:
                    hits = conn.execute("SELECT * FROM hits WHERE session_id=%s ORDER BY timestamp", (sid,)).fetchall()
                assert len(hits) == 2
                assert [hit["force_n"] for hit in hits] == [100, 600]
                assert [(len(hit["contact"]["columns"]), len(hit["contact"]["rows"])) for hit in hits] == [(2, 2), (6, 4)]
                assert all(hit["region"] is None for hit in hits)
                counters = app.extensions["udp_receiver"].snapshot()["counters"]
                assert counters["accepted"] == 2 and counters["duplicate"] == 1, counters
                await main.bring_to_front()
                await expect(main.locator("#hit-count")).to_have_text("2")
                await expect(main.locator("#average-force")).to_have_text("350")
                await expect(main.locator("#maximum-force")).to_have_text("600")
                await expect(main.locator("#vest")).to_have_attribute("data-spatial-hits", "2")
                await expect(main.locator("#hit-rows tr")).to_have_count(2)
                (ROOT / ".local").mkdir(exist_ok=True)
                await main.screenshot(path=str(ROOT / ".local/grid-dashboard.png"), full_page=True)
                await sender.screenshot(path=str(ROOT / ".local/interactive-simulator.png"), full_page=True)
                await main.reload()
                await expect(main.locator("#vest")).to_have_attribute("data-spatial-hits", "2")
                await main.locator("#end-session").click()
                await expect(main.locator("#view-mode")).to_have_text("SAVED SESSION")
                await sender.bring_to_front()
                await expect(sender.locator("#target-vest")).to_have_attribute("aria-disabled", "true")
                assert not errors, errors
                print("PASS: two websites, different click locations, 2x2 and 6x4 contacts, independent forces, real UDP, deduplication, saved history and inactive gating.")
                print("Database/browser: 2 hits, average 350 N, max 600 N. Screenshots in .local/.")
            finally:
                await browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run_isolated(verify, with_udp=True)
