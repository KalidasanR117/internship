import time
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

URL = "https://fixthephoto.com/online-gimp-editor.html"

def wait_for_editor(driver):
    print("[INFO] Waiting for editor iframe...")
    WebDriverWait(driver, 20).until(
        EC.frame_to_be_available_and_switch_to_it((By.ID, "ps-iframe"))
    )
    print("[INFO] Switched to Editor iframe")

def create_new_canvas(driver):
    print("[INFO] Creating new canvas (800x600)...")
    wait = WebDriverWait(driver, 10)
    
    # 1. Click "File"
    file_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space()='File']")))
    file_btn.click()
    
    # 2. Click "New..."
    new_item = wait.until(EC.element_to_be_clickable((By.XPATH, "//span[text()='New...']/parent::div")))
    new_item.click()
    time.sleep(1) 
    
    # 3. Explicitly set Width and Height 
    # (Crucial to ensure the canvas is big enough for our drawing coordinates)
    numeric_inputs = driver.find_elements(By.XPATH, "//input[@inputmode='numeric']")
    if len(numeric_inputs) >= 2:
        numeric_inputs[0].click()
        numeric_inputs[0].send_keys(Keys.CONTROL, "a")
        numeric_inputs[0].send_keys("800") # Width
        
        numeric_inputs[1].click()
        numeric_inputs[1].send_keys(Keys.CONTROL, "a")
        numeric_inputs[1].send_keys("600") # Height
    
    time.sleep(0.5)

    create_btn = driver.find_element(
    By.XPATH, "//button[normalize-space()='Create']"
)
    create_btn.click()

    
    print("[INFO] New canvas created")
    time.sleep(2) 

def draw_rectangle_with_free_pen(driver):
    print("[INFO] Selecting Free Pen...")

    wait = WebDriverWait(driver, 15)
    actions = ActionChains(driver)

    # 1. Locate Pen tool
    pen_tool = wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//button[contains(@class,'toolbtn') and contains(@title,'Pen')]")
        )
    )

    # 2. Right-click Pen tool to open menu
    actions.context_click(pen_tool).perform()
    time.sleep(1.0) # Wait for menu to appear

    # 3. Click "Free Pen" using JavaScript
    # We use a broad text match because the menu structure can be complex
    free_pen_option = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, "//span[normalize-space()='Free Pen']/parent::div")
        )
    ).click()
    
  

    # 4. Draw rectangle
    # We target the main canvas specifically
    canvas = wait.until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "canvas.mainCanvas")
        )
    )

    # Scroll canvas into view (Fixes 'out of bounds' error)
    driver.execute_script("arguments[0].scrollIntoView();", canvas)
    time.sleep(0.5)

    print("[INFO] Drawing...")

    # Start drawing at (50, 50) which is safe for an 800x600 canvas
    # Drawing a 200x150 rectangle
    (
        actions.move_to_element_with_offset(canvas, 50, 50)
            .click_and_hold()
            .pause(0.1)
            .move_by_offset(200, 0)
            .pause(0.1)
            .move_by_offset(0, 150)
            .pause(0.1)
            .move_by_offset(-200, 0)
            .pause(0.1)
            .move_by_offset(0, -150)
            .pause(0.1)
            .release()
            .perform()
    )

    actions.reset_actions()
    print("[INFO] Rectangle drawn using Free Pen")

def main():
    options = Options()
    options.add_argument("--start-maximized")
    
    service = Service("chromedriver.exe") 
    driver = webdriver.Chrome(service=service, options=options)

    try:
        driver.get(URL)
        wait_for_editor(driver)
        create_new_canvas(driver)
        draw_rectangle_with_free_pen(driver)
        
        # Keep browser open to see the result
        time.sleep(10)
        
    except Exception as e:
        print(f"[ERROR] An error occurred: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()