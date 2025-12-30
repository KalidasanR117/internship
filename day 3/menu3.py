from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from urllib.parse import urlparse
from selenium.common.exceptions import TimeoutException
import time

START_URL = "https://developer.mozilla.org/en-US/"
# START_URL = "https://www.geeksforgeeks.org/"
# START_URL = "https://www.w3schools.com/"

WAIT_TIME = 10
MAX_LINKS = 30

service = Service("chromedriver.exe")
driver = webdriver.Chrome(service=service)
wait = WebDriverWait(driver, WAIT_TIME)
# driver.maximize_window()


def get_domain(url):
    return urlparse(url).netloc

def js_click(driver, element):
    driver.execute_script("arguments[0].click();", element)

def get_nav_links(driver, base_domain):
    links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
    results = []
    seen = set()

    for link in links:
        href = link.get_attribute("href")
        text = link.text.strip()

        if not href or not text:
            continue

        if href.startswith("javascript"):
            continue

        parsed = urlparse(href)
        if parsed.netloc != base_domain:
            continue

        if href not in seen:
            results.append((text, href))
            seen.add(href)

        if len(results) >= MAX_LINKS:
            break

    return results



def get_navbar_buttons_safe(driver):
    try:
        nav = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "nav.tnb-desktop-nav"))
        )
        buttons = nav.find_elements(By.CSS_SELECTOR, "a.tnb-nav-btn")
        return buttons if buttons else None
    except TimeoutException:
        return None

def get_dropdown_links(driver, base_domain, collected):
    time.sleep(1)

    links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
    results = []

    for link in links:
        href = link.get_attribute("href")
        text = link.text.strip()

        if not href or not text:
            continue

        if href.startswith("javascript"):
            continue

        parsed = urlparse(href)
        if parsed.netloc != base_domain:
            continue

        if href not in collected:
            results.append((text, href))
            collected.add(href)

        if len(collected) >= MAX_LINKS:
            break

    return results



try:
    driver.get(START_URL)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    
    base_domain = get_domain(START_URL)
    collected_urls = set()
    all_nav_links = []

    navbar_buttons = get_navbar_buttons_safe(driver)

    if navbar_buttons:
        print("JS Navbar detected (W3Schools style)")

        for btn in navbar_buttons:
            menu_name = btn.text.strip()
            if not menu_name:
                continue

            print(f"\nOPEN MENU: {menu_name}")
            js_click(driver, btn)

            links = get_dropdown_links(driver, base_domain, collected_urls)
            for text, url in links:
                print(f"  - {text}")
                all_nav_links.append((text, url))

            js_click(driver, btn)

            if len(collected_urls) >= MAX_LINKS:
                break
    else:
        print("No JS navbar detected — using full-page links")
        all_nav_links = get_nav_links(driver, base_domain)

    
    HOME_URL = START_URL

    for text, url in all_nav_links:
        print(f"\nOPEN PAGE: {text}")
        driver.get(url)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(2)

        print("RETURNING TO HOME")
        driver.get(HOME_URL)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(1)


finally:
    driver.quit()
