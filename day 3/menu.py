from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import time



service = Service("chromedriver.exe")
driver = webdriver.Chrome(service=service)
wait = WebDriverWait(driver, 10)

# BASE_URL = "https://www.geeksforgeeks.org/"
BASE_URL ="https://www.w3schools.com/"

driver.get(BASE_URL)
time.sleep(3)

soup = BeautifulSoup(driver.page_source, "html.parser")

li_items = soup.find_all("li")

visited = set()
wait.until(
    EC.element_to_be_clickable((
       By.CLASS_NAME, "hamburgerMenu"
    ))
).click()
for li in li_items:
    a = li.find("a", href=True)
    if not a:
        continue

    href = a["href"]
    full_url = urljoin(BASE_URL, href)

    if full_url in visited:
        continue

    if not full_url.startswith(BASE_URL):
        continue

    visited.add(full_url)


    driver.get(full_url)
    time.sleep(1)
    driver.back()
    time.sleep(1)

time.sleep(5)
driver.quit()
