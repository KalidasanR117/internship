from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

# ================= CONFIG =================
options = webdriver.ChromeOptions()
options.add_argument("--start-maximized")

service = Service("D:/Internship/Selenium/chromedriver.exe")
driver = webdriver.Chrome(service=service, options=options)
wait = WebDriverWait(driver, 5)

driver.get("https://onlinenotepad.org/notepad")
time.sleep(2)

def get_editor():
    wait.until(
    EC.frame_to_be_available_and_switch_to_it((By.CSS_SELECTOR, "iframe"))
)
    return  wait.until(
    EC.presence_of_element_located((By.ID, "tinymce"))
)

# try:
# ================= EDITOR TEST =================
editor = get_editor()
editor.clear()
editor.send_keys("This text is typed by Selenium!")
print("✅ Text typed")

# ================= FILE MENU =================
def open_file_menu():
    file_menu = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[title='File']")))
    file_menu.click()

open_file_menu()
wait.until(EC.element_to_be_clickable((By.XPATH, "//a[normalize-space()='New']"))).click()
time.sleep(1)

editor = get_editor()
editor.send_keys("Testing Save and Print\n")

open_file_menu()
wait.until(EC.element_to_be_clickable((By.XPATH, "//a[normalize-space()='Save']"))).click()
time.sleep(1)
print("⚠️ Save clicked (dialog ignored)")

open_file_menu()
wait.until(EC.element_to_be_clickable((By.XPATH, "//a[normalize-space()='Print']"))).click()
time.sleep(2)
print("⚠️ Print clicked (preview ignored)")

# ================= EDIT SHORTCUTS =================
editor = get_editor()
editor.send_keys(Keys.CONTROL, "a")
editor.send_keys(Keys.CONTROL, "x")
time.sleep(1)
editor.send_keys(Keys.CONTROL, "v")
time.sleep(1)
editor.send_keys(Keys.CONTROL, "z")
time.sleep(1)
editor.send_keys(Keys.CONTROL, "y")
time.sleep(1)

print("✅ Edit shortcuts tested")

# ================= TOOLBAR BUTTONS =================
wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-command*='new']"))).click()
time.sleep(1)

wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-command*='undo']"))).click()
wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-command*='redo']"))).click()

print("✅ Toolbar actions tested")

# ================= FULLSCREEN =================
fullscreen = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[title='Fullscreen']")))
fullscreen.click()
time.sleep(1)
fullscreen.click()
print("✅ Fullscreen toggled")

# ================= FONT SETTINGS =================
font_family = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "select[class*='font']")))
font_family.click()
font_family.find_elements(By.TAG_NAME, "option")[1].click()
time.sleep(1)

font_size = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "select[class*='size']")))
font_size.click()
font_size.find_elements(By.TAG_NAME, "option")[2].click()
time.sleep(1)

print("✅ Font formatting tested")

print("\n🎉 ALL UI TESTS COMPLETED SUCCESSFULLY")

    # except Exception as e:
    #   print("\n❌ TEST FAILED:", e)

    # finally:
time.sleep(3)
driver.quit()