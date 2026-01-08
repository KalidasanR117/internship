import os
import time
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager


TARGET_URL = "https://fixthephoto.com/online-gimp-editor.html"
OUTPUT_DIR = "web_crawler"
OUTPUT_FILE = "index.html"

PAGE_LOAD_TIMEOUT = 20
HEADLESS = False   
os.makedirs(OUTPUT_DIR, exist_ok=True)

def create_driver():
    chrome_options = Options()
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-infobars")

    if HEADLESS:
        chrome_options.add_argument("--headless=new")

    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options
    )

driver = create_driver()
wait = WebDriverWait(driver, PAGE_LOAD_TIMEOUT)

try:
    print("[INFO] Opening website...")
    driver.get(TARGET_URL)

    
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

    
    time.sleep(2)

    print("[INFO] Page fully loaded")

  
    html = driver.page_source
    soup = BeautifulSoup(html, "html.parser")

    file_path = os.path.join(OUTPUT_DIR, OUTPUT_FILE)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(soup.prettify())

    print(f"[SUCCESS] Rendered HTML saved at: {file_path}")

except TimeoutException:
    print("[ERROR] Page took too long to load")

except Exception as e:
    print(f"[ERROR] Unexpected error: {e}")

finally:
    time.sleep(3)
    driver.quit()
    print("[INFO] Browser closed")
