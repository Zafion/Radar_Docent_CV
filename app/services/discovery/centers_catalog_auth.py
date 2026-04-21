from __future__ import annotations

from playwright.sync_api import sync_playwright

PAGE_URL = "https://ceice.gva.es/es/web/admision-alumnado/centres-educatius"
LOCALSTORAGE_TOKEN_KEY = "token"


class CentersCatalogAuthError(RuntimeError):
    pass


def obtain_centers_catalog_token(
    *,
    page_url: str = PAGE_URL,
    timeout_ms: int = 30000,
    headless: bool = True,
) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_function(
                f"() => !!window.localStorage.getItem({LOCALSTORAGE_TOKEN_KEY!r})",
                timeout=timeout_ms,
            )
            token = page.evaluate(
                f"() => window.localStorage.getItem({LOCALSTORAGE_TOKEN_KEY!r})"
            )
        finally:
            browser.close()

    if not token or not str(token).startswith("Bearer "):
        raise CentersCatalogAuthError("No se encontró un token JWT válido en localStorage.token")

    return str(token)
