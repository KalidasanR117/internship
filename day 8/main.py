from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
import time


START_URL = "https://onlinenotepad.org/notepad"
WAIT_TIME = 5


driver = webdriver.Chrome(service=Service("chromedriver.exe"))
wait = WebDriverWait(driver, WAIT_TIME)


def select_editor():
    wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "editor_ifr")))
    time.sleep(0.5)
    editor = wait.until(EC.element_to_be_clickable((By.ID, "tinymce")))
    return editor

def click_menu(driver, wait, menu_name):
    try:
        menu_button = wait.until(
            EC.element_to_be_clickable((By.XPATH, f"//button[span[text()='{menu_name}']]"))
        )
        ActionChains(driver).move_to_element(menu_button).click().perform()
        time.sleep(0.3)
        print(f"Clicked menu: {menu_name}")
    except Exception as e:
        print(f"Failed to click menu '{menu_name}': {e}")

def inject_text(editor, text):
    editor.click()
    editor.send_keys(Keys.CONTROL, "a")
    editor.send_keys(Keys.DELETE)
    editor.send_keys(text)

def select_submenu(driver, submenu_name):
    items = driver.find_elements(By.CSS_SELECTOR, ".mce-menu-item")
    target = None
    for item in items:
        if item.is_displayed():
            span = item.find_elements(By.CSS_SELECTOR, "span.mce-text")
            name = span[0].text.strip() if span else ""
            if name == submenu_name:
                target = item
                break
    if target:
        driver.execute_script("arguments[0].scrollIntoView(true);", target)
        ActionChains(driver).move_to_element(target).click().perform()
        time.sleep(0.3)
        return True
    return False


driver.get(START_URL)
wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))


driver.switch_to.frame(driver.find_element(By.ID, "editor_ifr"))
editor = driver.find_element(By.ID, "tinymce")
inject_text(editor, "Testing menus.")
driver.switch_to.default_content()
time.sleep(0.3)


file_submenus = ["New document", "Save", "Open...", "Print"]

click_menu(driver, wait, "File")
for submenu in file_submenus:
    if select_submenu(driver, submenu):
        print(f"File submenu: {submenu}")
       
        if submenu == "Save":
            try:
                input_box = wait.until(EC.presence_of_element_located((By.ID, "txt_name")))
                input_box.send_keys("MyTestFile")
                save_btn = driver.find_element(By.XPATH, "//button[span[text()='Save']]")
                save_btn.click()
                time.sleep(0.3)
                print("File 'MyTestFile' saved successfully.")
            except Exception as e:
                print(f"Failed Save: {e}")
    driver.find_element(By.TAG_NAME, "body").click()
    time.sleep(0.2)
    click_menu(driver, wait, "File")
driver.find_element(By.TAG_NAME, "body").click()
time.sleep(0.5)


edit_submenus = ["Select all", "Copy", "Paste", "Cut"]

driver.switch_to.frame(driver.find_element(By.ID, "editor_ifr"))
editor = driver.find_element(By.ID, "tinymce")
editor.click()
driver.switch_to.default_content()

for submenu in edit_submenus:
    click_menu(driver, wait, "Edit")
    if select_submenu(driver, submenu):
        print(f"Edit submenu: {submenu}")
        driver.switch_to.frame(driver.find_element(By.ID, "editor_ifr"))
        editor.click()
        if submenu == "Select all":
            editor.send_keys(Keys.CONTROL, "a")
        elif submenu == "Copy":
            editor.send_keys(Keys.CONTROL, "c")
        elif submenu == "Paste":
            editor.send_keys(Keys.CONTROL, "v")
        elif submenu == "Cut":
            editor.send_keys(Keys.CONTROL, "x")
            editor.send_keys(Keys.CONTROL, "v")
        driver.switch_to.default_content()
    driver.find_element(By.TAG_NAME, "body").click()
    time.sleep(0.2)


view_submenus = ["Fullscreen"]
click_menu(driver, wait, "View")
for submenu in view_submenus:
    if select_submenu(driver, submenu):
        print(f"View submenu: {submenu}")
    driver.find_element(By.TAG_NAME, "body").click()
    time.sleep(0.2)


help_submenus = ["Keyboard Shortcuts", "Homepage"]
click_menu(driver, wait, "Help")
for submenu in help_submenus:
    if select_submenu(driver, submenu):
        print(f"Help submenu: {submenu}")
    driver.find_element(By.TAG_NAME, "body").click()
    time.sleep(0.2)

font_menu_button = wait.until(
    EC.element_to_be_clickable((By.ID, "mceu_10-open"))
)
font_menu_button.click()

wait.until(
    lambda d: d.find_element(By.ID, "mceu_10")
              .get_attribute("aria-expanded") == "true"
)
def select_font(font_name):
    font_item = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, f"//div[contains(@class,'mce-menu-item') and normalize-space()='{font_name}']")
        )
    )
    font_item.click()
def select_font_size(driver, wait, size_text):
    item = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, f"//div[contains(@class,'mce-menu-item') and normalize-space()='{size_text}']")
        )
    )
    item.click()

def ensure_menu_open(driver, wait, menu_id, button_id):
    menu = driver.find_element(By.ID, menu_id)

    if menu.get_attribute("aria-expanded") != "true":
        button = wait.until(
            EC.element_to_be_clickable((By.ID, button_id))
        )
        button.click()

        wait.until(
            lambda d: d.find_element(By.ID, menu_id)
                      .get_attribute("aria-expanded") == "true"
        )

# font_sizes = ["8pt", "10pt", "12pt", "14pt"]

# for size in font_sizes:
ensure_menu_open(driver, wait, "mceu_11", "mceu_11-open")
#     select_font_size(driver, wait, size)


time.sleep(5)
driver.quit()
print("All menus tested successfully.")
