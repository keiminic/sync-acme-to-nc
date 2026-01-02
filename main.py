from datetime import datetime
from playwright.async_api import async_playwright, Page, BrowserContext, expect, ViewportSize
from urllib.parse import urlparse
import asyncio
import logging
import os
import pyotp
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
logger = logging.getLogger("NetcupAutomation")


def get_env(key: str, required: bool = True, default: str = None):
    value = os.getenv(key, default)
    if required and not value:
        logger.error(f"Missing required environment variable: {key}")
        sys.exit(1)
    return value


NC_USER = get_env("NC_USER")
NC_PASS = get_env("NC_PASS")
NC_2FA_SECRET = get_env("NC_2FA_SECRET")
NC_PRODUCT_ID = get_env("NC_PRODUCT_ID")
NC_DOMAIN = get_env("NC_DOMAIN")
NC_CCP = get_env("NC_CCP", required=False, default="https://www.customercontrolpanel.de")
SSL_PRIVATE_KEY = get_env("SSL_PRIVATE_KEY", default="/data/key.pem")
SSL_CERT_KEY = get_env("SSL_CERT_KEY", default="/data/cert.pem")

MAILHOSTING_ID = None
WEBHOSTING_ID = None

MAIN_WEB_ID = None
ALL_WEB_IDS = []
MAIL_ID = None


async def read_file_content(path: str) -> str:
    logger.info("Reading certificate file content")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        logger.error(f"Cert not found: {path}")
        raise


async def select_pul_dropdown(page: Page, label_text: str, option_text: str):
    logger.info(f"Operating dropdown: '{label_text}', target option: '{option_text}'")

    await page.get_by_label(label_text, exact=True).click()

    option_locator = page.locator(".pul-menu__base-item-content", has_text=option_text)

    await option_locator.first.click()


async def handle_login(page: Page):
    logger.info("Starting login to Netcup CCP...")
    await page.goto(f"{NC_CCP}?login_language=en-US")

    await page.get_by_placeholder("Customer number").fill(NC_USER)
    await page.get_by_placeholder("Password").fill(NC_PASS)
    await page.get_by_role("button", name="Log in").click()

    await page.wait_for_load_state("networkidle")

    if "start.php" in page.url or "verification" in await page.content():
        logger.info("2FA challenge detected, generating verification code...")
        totp = pyotp.TOTP(NC_2FA_SECRET)
        token = totp.now()

        await page.get_by_placeholder("TAN").fill(token)
        await page.get_by_role("button", name="Confirm token").click()
        await page.wait_for_url("**/start.php")
        logger.info("2FA verification successful")


async def process_sso_button(context: BrowserContext, page: Page, button_name: str):
    logger.info(f"Clicking {button_name}...")
    try:
        btn = page.get_by_text(button_name, exact=True)
        if not await btn.is_visible():
            raise Exception(f"Button {button_name} not found.")

        async with context.expect_page() as new_page_info:
            await btn.click()
    except Exception as e:
        raise Exception(f"Failed to {button_name}: {e}")

    new_page = await new_page_info.value
    logger.info(f"New tab opened, waiting for page load: {new_page.url}")

    try:
        await new_page.wait_for_load_state("networkidle", timeout=60000)
    except Exception as e:
        raise Exception(f"Page load timed out: {e}")

    current_url = new_page.url
    logger.info(f"Captured URL: {current_url}")

    extracted_id = None
    try:
        parsed_url = urlparse(current_url)
        hostname = parsed_url.hostname

        if hostname:
            extracted_id = hostname.split('.')[0]
            logger.info(f"Extracted Host ID from URL: {extracted_id}")
        else:
            logger.error("Could not parse hostname from URL")

    except Exception as e:
        logger.error(f"Failed to extract ID from URL: {e}")

    if button_name.endswith("WEB"):
        global WEBHOSTING_ID
        WEBHOSTING_ID = extracted_id
        await get_web_internal_ids(new_page)
    elif button_name.endswith("MAIL"):
        global MAILHOSTING_ID
        MAILHOSTING_ID = extracted_id
        await get_mail_internal_id(new_page)

    logger.info(f"Closing tab: {button_name}")
    await new_page.close()


async def trigger_auto_login(context: BrowserContext, page: Page):
    logger.info("Navigating to products page...")
    await page.goto(f"{NC_CCP}/produkte.php")

    logger.info(f"Opening product details, ID: {NC_PRODUCT_ID}")
    await page.wait_for_selector("tr", state="attached")
    xpath_selector = f"//tr[contains(., '{NC_PRODUCT_ID}')]//a[contains(@onclick, 'showProductDetail')]"
    element_count = await page.locator(xpath_selector).count()
    if element_count == 0:
        logger.error(f"Failed to find row via XPath: {xpath_selector}")
        raise Exception(f"Product row for '{NC_PRODUCT_ID}' not found")
    await page.locator(xpath_selector).first.click()

    await page.wait_for_load_state("networkidle", timeout=60000)

    await process_sso_button(context, page, "Auto-Login WEB")
    await process_sso_button(context, page, "Auto-Login MAIL")


async def get_mail_internal_id(page: Page):
    target_url = f"https://{MAILHOSTING_ID}.webhosting.systems/smb/mail-settings/list"
    logger.info(f"Navigating to Mail List to find ID for {NC_DOMAIN}: {target_url}")

    await page.goto(target_url)
    await page.wait_for_load_state("networkidle")

    row = page.locator("tr").filter(has_text=NC_DOMAIN)

    if await row.count() == 0:
        logger.error(f"Mail domain '{NC_DOMAIN}' not found in the list.")
        raise Exception("Mail ID not found")

    global MAIL_ID
    checkbox = row.locator("input[type='checkbox']")
    MAIL_ID = await checkbox.get_attribute("value")

    if not MAIL_ID:
        raise Exception("Found mail row but could not extract ID from checkbox value")

    logger.info(f"Detected NC_MAIL_ID: {MAIL_ID}")


async def get_web_internal_ids(page: Page):
    logger.info(f"Navigating to Web View to find IDs for {NC_DOMAIN}")

    global MAIN_WEB_ID, ALL_WEB_IDS

    data_obj = await page.evaluate(
        """
        () => {
            try {
                const scripts = Array.from(document.querySelectorAll('body script'));
                const targetScript = scripts.find(s => s.innerText.includes('Plesk.run') && s.innerText.includes('siteJetBannerProps'));

                if (targetScript) {
                    const raw = targetScript.innerText;
                    const startMarker = 'Plesk.run(';
                    const startIndex = raw.indexOf(startMarker);

                    if (startIndex !== -1) {
                        let jsonStr = raw.substring(startIndex + startMarker.length);
                        const lastIndex = jsonStr.lastIndexOf('});');
                        if (lastIndex !== -1) {
                            jsonStr = jsonStr.substring(0, lastIndex + 1);
                            return JSON.parse(jsonStr);
                        }
                    }
                }
                return null;
            } catch (e) {
                return {error: e.toString()};
            }
        }
        """)

    if not data_obj:
        logger.error("JS execution returned null. Could not find Plesk data object.")
        raise Exception("Failed to retrieve Plesk data via JS")

    if "error" in data_obj:
        logger.error(f"JS execution error: {data_obj['error']}")
        raise Exception("JS execution failed inside browser")
    try:
        level1 = data_obj.get("data", {})
        if not level1:
            level1 = data_obj

        level2 = level1.get("data", {})
        domains_list = level2.get("data", [])

        if not isinstance(domains_list, list):
            domains_list = level1.get("data", [])

        if not isinstance(domains_list, list):
            logger.error(f"Could not locate domain list in JSON object. Keys: {data_obj.keys()}")
            raise Exception("JSON structure mismatch")

        found_main = False
        ALL_WEB_IDS = []

        logger.info(f"Processing {len(domains_list)} domains from Browser JSON...")

        for item in domains_list:
            d_name = item.get("displayName")
            d_id = str(item.get("domainId"))

            if d_name == NC_DOMAIN:
                MAIN_WEB_ID = d_id
                ALL_WEB_IDS.append(d_id)
                found_main = True
                logger.info(f"-> Global MAIN_WEB_ID set: {MAIN_WEB_ID} ({d_name})")

            elif d_name.endswith(f".{NC_DOMAIN}"):
                ALL_WEB_IDS.append(d_id)
                logger.info(f"-> Subdomain ID found: {d_id} ({d_name})")

        if not found_main:
            if ALL_WEB_IDS:
                logger.warning(f"Main domain {NC_DOMAIN} not found, utilizing first subdomain as fallback.")
                MAIN_WEB_ID = ALL_WEB_IDS[0]
            else:
                raise Exception(f"Target domain {NC_DOMAIN} not found in Plesk data.")

    except Exception as e:
        logger.error(f"Error parsing returned JSON data: {e}")
        raise


async def upload_certificate(page: Page, target_url: str, cert_name: str, key_data: str, cert_data: str):
    logger.info(f"Uploading certificate to: {target_url}")
    await page.goto(target_url)

    if "list" in target_url:
        logger.info("Currently on the list page, clicking the add certificate button...")
        add_btn = page.get_by_text("Add SSL/TLS Certificate")
        if await add_btn.is_visible():
            await add_btn.click()
            await page.wait_for_load_state("networkidle")

    logger.info(f"Filling in certificate name: {cert_name}")
    await page.locator('input[name="name"]').fill(cert_name)

    logger.info("Entering private key...")
    await page.locator('textarea[name="uploadText[privateKeyText]"]').fill(key_data)

    logger.info("Entering certificate content...")
    await page.locator('textarea[name="uploadText[certificateText]"]').fill(cert_data)

    logger.info("Submitting certificate...")
    await page.locator("#btn-uploadText-sendText").click()

    try:
        await expect(page.get_by_text("Information: The SSL/TLS certificate was issued.")).to_be_visible(timeout=30000)
    except:
        logger.warning("Success message not detected, continuing execution")


async def secure_mail_action(page: Page, list_url: str, cert_name: str):
    logger.info(f"Executing Secure Mail operation:: {list_url}")
    await page.goto(list_url)

    logger.info(f"Locating certificate row: {cert_name}")
    target_row = page.locator("tr", has_text=cert_name)

    await target_row.locator("input[type='checkbox']").check()

    logger.info("Clicking Secure Mail button...")
    await page.locator("#buttonMailCertificate").click()

    await page.wait_for_load_state("networkidle")


async def update_hosting_settings(page: Page, settings_url: str, cert_name: str):
    logger.info(f"Updating Hosting Settings: {settings_url}")
    await page.goto(settings_url)

    logger.info(f"Selecting certificate: {cert_name}")
    await select_pul_dropdown(page, "Certificate", cert_name)

    logger.info("Saving settings...")
    await page.get_by_role("button", name="Save").click()

    await page.wait_for_load_state("networkidle")


async def update_mail_settings(page: Page, settings_url: str, cert_name: str):
    logger.info(f"Updating Mail Settings: {settings_url}")
    await page.goto(settings_url)

    logger.info("Setting Webmail certificate...")
    await select_pul_dropdown(page, "SSL/TLS certificate for webmail", cert_name)

    logger.info("Setting mail certificate...")
    await select_pul_dropdown(page, "SSL/TLS certificate for mail", cert_name)

    await page.get_by_role("button", name="Save").click()
    await page.wait_for_load_state("networkidle")


async def main():
    date_str = datetime.now().strftime("%Y%m%d")
    cert_name = f"acme-{NC_DOMAIN}{date_str}"
    logger.info(f"Certificate name for this run: {cert_name}")

    key_content = await read_file_content(SSL_PRIVATE_KEY)
    cert_content = await read_file_content(SSL_CERT_KEY)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            slow_mo=100,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"],
        )

        viewport: ViewportSize = {"width": 1920, "height": 1080}
        context = await browser.new_context(
            viewport=viewport,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            # Step 1, login and create cookie session for webhosting and mailhosting
            await handle_login(page)
            await trigger_auto_login(context, page)

            # Step 2, upload ssl
            await upload_certificate(
                page,
                f"https://{WEBHOSTING_ID}.webhosting.systems/smb/ssl-certificate/add/id/{MAIN_WEB_ID}",
                cert_name, key_content, cert_content
            )
            await upload_certificate(
                page,
                f"https://{MAILHOSTING_ID}.webhosting.systems/smb/ssl-certificate/add/id/{MAIL_ID}",
                cert_name, key_content, cert_content
            )

            # Step 3, set ssl for root, subdomain and mail
            ## webhosting
            await secure_mail_action(
                page,
                f"https://{WEBHOSTING_ID}.webhosting.systems/smb/ssl-certificate/list/id/{MAIN_WEB_ID}",
                cert_name
            )
            for sub_id in ALL_WEB_IDS:
                await update_hosting_settings(
                    page,
                    f"https://{WEBHOSTING_ID}.webhosting.systems/smb/web/view/{sub_id}/hosting-settings",
                    cert_name
                )
            ## mailhosting
            await secure_mail_action(
                page,
                f"https://{MAILHOSTING_ID}.webhosting.systems/smb/ssl-certificate/list/id/{MAIL_ID}",
                cert_name
            )
            await update_mail_settings(
                page,
                f"https://{MAILHOSTING_ID}.webhosting.systems/smb/mail-settings/edit/id/{MAIL_ID}/domainId/{MAIL_ID}",
                cert_name
            )

            logger.info("All tasks completed successfully.")

        except Exception as e:
            logger.error(f"Task execution failed: {e}")
            await page.screenshot(path="error_screenshot.png")
            raise
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
