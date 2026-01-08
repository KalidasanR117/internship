from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
import time
import json

options = Options()
options.add_argument("--start-maximized")

service = Service("chromedriver.exe") 
driver = webdriver.Chrome(service=service, options=options)

driver.get("https://onlinenotepad.org/notepad")
time.sleep(3) 

ui_snapshot = {
    "url": driver.current_url,
    "title": driver.title,
    "inputs": [],
    "buttons": [],
    "links": []
}

# --- INPUT FIELDS ---
for inp in driver.find_elements(By.TAG_NAME, "input"):
    ui_snapshot["inputs"].append({
        "tag": "input",
        "type": inp.get_attribute("type"),
        "name": inp.get_attribute("name"),
        "placeholder": inp.get_attribute("placeholder"),
        "required": inp.get_attribute("required") is not None
    })

# --- BUTTONS ---
for btn in driver.find_elements(By.TAG_NAME, "button"):
    ui_snapshot["buttons"].append({
        "tag": "button",
        "text": btn.text.strip(),
        "type": btn.get_attribute("type")
    })

# --- LINKS ---
for link in driver.find_elements(By.TAG_NAME, "a"):
    text = link.text.strip()
    if text: 
        ui_snapshot["links"].append({
            "tag": "a",
            "text": text,
            "href": link.get_attribute("href")
        })

print(json.dumps(ui_snapshot, indent=2))

driver.quit()
