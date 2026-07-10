"""Executable browser tests for the Dynamic page policy."""

from __future__ import annotations

import pytest
from playwright.async_api import async_playwright

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.dynamic_page import DynamicPagePolicy


@pytest.mark.asyncio
async def test_dynamic_page_policy_is_idempotent_on_real_dom():
    policy = DynamicPagePolicy(
        CrawlerConfig(
            scan_full_page=False,
            process_iframes=True,
            flatten_shadow_dom=True,
            remove_overlay_elements=True,
            remove_consent_popups=True,
        )
    )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 800, "height": 600})
        await page.set_content(
            """
            <div id="overlay" role="dialog" style="position:fixed;inset:0;z-index:20">
              Accept privacy consent
            </div>
            <iframe srcdoc='<p>magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA</p>'></iframe>
            <div id="shadow-host"></div>
            <script>
              document.querySelector('#shadow-host').attachShadow({mode: 'open'}).innerHTML =
                '<p>magnet:?xt=urn:btih:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB</p>';
            </script>
            """
        )

        await policy.prepare(page)
        await policy.prepare(page)

        assert await page.locator("#overlay").count() == 0
        assert await page.locator("[data-magnet-harvester-extra]").count() == 1
        extracted = await page.locator("[data-magnet-harvester-extra]").text_content()
        assert extracted.count("urn:btih:AAAAAAAA") == 1
        assert extracted.count("urn:btih:BBBBBBBB") == 1
        await browser.close()
