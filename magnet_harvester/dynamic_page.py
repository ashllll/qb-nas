"""Executable browser policy applied before crawler HTML extraction."""

from __future__ import annotations

from magnet_harvester.config import CrawlerConfig


class DynamicPagePolicy:
    """Prepare a browser page through one idempotent interface."""

    def __init__(self, config: CrawlerConfig):
        self._config = config

    async def prepare(self, page) -> None:
        if self._config.remove_overlay_elements or self._config.remove_consent_popups:
            await page.evaluate(
                """
                ({ removeOverlays, removeConsent }) => {
                    const shouldRemove = (el) => {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        const text = (el.innerText || "").toLowerCase();
                        const looksModal = el.matches("dialog,[aria-modal='true'],[role='dialog']");
                        const blocksPage =
                            ["fixed", "sticky"].includes(style.position) &&
                            rect.width * rect.height > window.innerWidth * window.innerHeight * 0.25 &&
                            Number(style.zIndex || 0) >= 10;
                        const looksConsent =
                            /cookie|consent|privacy|同意|隐私|接受|accept|agree/.test(text);
                        const floats = looksModal || blocksPage || ["fixed", "sticky"].includes(style.position);
                        return looksModal || (removeOverlays && blocksPage) || (removeConsent && floats && looksConsent);
                    };
                    document.querySelectorAll("dialog,[aria-modal='true'],[role='dialog'],body *")
                        .forEach((el) => {
                            if (shouldRemove(el)) el.remove();
                        });
                    document.documentElement.style.overflow = "auto";
                    document.body.style.overflow = "auto";
                }
                """,
                {
                    "removeOverlays": self._config.remove_overlay_elements,
                    "removeConsent": self._config.remove_consent_popups,
                },
            )

        if self._config.scan_full_page:
            for _ in range(max(0, self._config.max_scroll_steps)):
                await page.evaluate("() => window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(int(self._config.scroll_delay * 1000))

        if self._config.process_iframes or self._config.flatten_shadow_dom:
            await page.evaluate(
                """
                ({ processIframes, flattenShadowDom }) => {
                    const selector = "[data-magnet-harvester-extra]";
                    const sink = document.querySelector(selector) || document.createElement("section");
                    sink.replaceChildren();
                    sink.setAttribute("data-magnet-harvester-extra", "");
                    sink.hidden = true;
                    const append = (html) => {
                        if (!html) return;
                        const block = document.createElement("div");
                        block.innerHTML = html;
                        sink.appendChild(block);
                    };
                    if (processIframes) {
                        document.querySelectorAll("iframe").forEach((frame) => {
                            try {
                                append(frame.contentDocument?.documentElement?.outerHTML);
                            } catch {}
                        });
                    }
                    if (flattenShadowDom) {
                        const walk = (node) => {
                            if (node.shadowRoot) {
                                append(node.shadowRoot.innerHTML);
                                stack.push(...node.shadowRoot.querySelectorAll("*"));
                            }
                        };
                        const stack = [...document.querySelectorAll("*")];
                        while (stack.length) {
                            walk(stack.pop());
                        }
                    }
                    if (!sink.isConnected) document.body?.appendChild(sink);
                }
                """,
                {
                    "processIframes": self._config.process_iframes,
                    "flattenShadowDom": self._config.flatten_shadow_dom,
                },
            )
