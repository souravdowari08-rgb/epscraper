import asyncio
import urllib.parse
from bs4 import BeautifulSoup

from quart import Quart, request, jsonify
from quart_cors import cors
from playwright.async_api import async_playwright

app = Quart(__name__)
app = cors(app, allow_origin="*")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

CHROME_ARGS = [
    "--disable-gpu",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--blink-settings=imagesEnabled=false",
]

_playwright = None
_browser = None
_context = None

async def extract_redirect_url(page):
    async def snap():
        return await page.evaluate(
            r"""(() => {
    for (const s of document.scripts) {
        const t = s.textContent || "";
        let m;
        if ((m = /c\.setAttribute\("href","([^"]+)"\)/.exec(t))) return m[1];
        if ((m = /window\.location(?:\.href)?\s*=\s*"([^"]+)"/.exec(t))) return m[1];
        if ((m = /location\.assign\(["']([^"']+)["']\)/.exec(t))) return m[1];
    }
    const cEl = document.getElementById("c");
    if (cEl && cEl.getAttribute("href")) return cEl.getAttribute("href");
    for (const a of document.querySelectorAll("a[href]")) {
        const h = a.getAttribute("href");
        if (!h) continue;
        if (h.includes("driveseed.org") || h.includes("/zfile/") ||
            h.includes("/wfile/") || h.includes("/file/")) return h;
    }
    return null;
})();"""
        )

    for _ in range(20):
        result = await snap()
        if result:
            return result
        await asyncio.sleep(1)
    return None


async def fetch_variant(context, key, url, selector):
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)

        try:
            element = await page.query_selector(selector)
            if element:
                href = await element.get_attribute("href")
                if href:
                    return key, href
        except:
            pass

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        a = soup.find("a", href=True)
        return key, (a["href"] if a else None)

    except Exception:
        return key, None
    finally:
        await page.close()


@app.before_serving
async def init_browser():
    global _playwright, _browser, _context

    if _playwright:
        return

    print("🚀 Starting Playwright browser...")

    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(headless=True, args=CHROME_ARGS)
    _context = await _browser.new_context(
        user_agent=UA, viewport={"width": 1280, "height": 720}
    )

    try:
        page = await _context.new_page()
        await page.goto("https://tech.unblockedgames.world/", timeout=30000)
        await asyncio.sleep(4)
        await page.close()
        print("🔥 Browser pre-warmed successfully.")
    except Exception as err:
        print(f"⚠️ Prewarm failed: {err}")

    print("✅ Browser ready")


@app.after_serving
async def close_browser():
    global _playwright, _browser, _context
    print("🧹 Cleaning up browser...")
    try:
        if _context:
            await _context.close()
        if _browser:
            await _browser.close()
        if _playwright:
            await _playwright.stop()
    except:
        pass
    print("✔️ Browser closed.")


@app.route("/getlink")
async def get_link():
    global _context

    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing ?url parameter"}), 400

    if not _context:
        return jsonify({"error": "Browser not ready"}), 503

    page = await _context.new_page()

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(1.5)

        try:
            await page.evaluate(
                "(()=>{const f=document.getElementById('landing');if(f&&f.submit)f.submit();})()"
            )
        except:
            pass

        await asyncio.sleep(2)

        redirect_url = await extract_redirect_url(page)
        if not redirect_url:
            return jsonify({
                "error": "Redirect not found",
                "debug_url": page.url,
                "debug_title": await page.title(),
            }), 504

        await page.goto(redirect_url, wait_until="domcontentloaded", timeout=25000)

        final_url = page.url
        file_id = urllib.parse.urlparse(final_url).path.split("/")[-1]

        variants = {
            "zfile": f"https://driveseed.org/zfile/{file_id}",
            "wfile_type1": f"https://driveseed.org/wfile/{file_id}?type=1",
            "wfile_type2": f"https://driveseed.org/wfile/{file_id}?type=2",
        }

        selectors = {
            "zfile": "#cf_captcha > div.card-body > div > a",
            "wfile_type1": "body > div > div > div.card-body > div > div > a",
            "wfile_type2": "body > div > div > div.card-body > div > div > a",
        }

        tasks = [
            fetch_variant(_context, key, variants[key], selectors[key])
            for key in variants
        ]
        download_results = dict(await asyncio.gather(*tasks))

        return jsonify({
            "final_url": final_url,
            "file_id": file_id,
            "download_links": download_results,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        await page.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
