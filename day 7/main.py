# from selenium import webdriver
# from selenium.webdriver.common.by import By
# from selenium.webdriver.chrome.service import Service
# from selenium.webdriver.chrome.options import Options
# import time
# import json

# options = Options()
# options.add_argument("--start-maximized")

# service = Service("chromedriver.exe") 
# driver = webdriver.Chrome(service=service, options=options)

# driver.get("https://fixthephoto.com/online-gimp-editor.html")



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
    time.sleep(8)

    iframe = driver.find_element(By.TAG_NAME, "iframe")
    driver.switch_to.frame(iframe)

    print("[INFO] Switched to Photopea iframe")


def create_new_canvas(driver):
    print("[INFO] Creating new canvas...")

    # Open "New" dialog
    file_btn = driver.find_element(
        By.XPATH, "//button[normalize-space()='File']"
    )
    file_btn.click()

    time.sleep(1)

    # Click New...
    new_item = driver.find_element(
        By.XPATH, "//span[text()='New...']/parent::div"
    )
    new_item.click()
    time.sleep(2)

    filename_input = driver.find_element(
    By.XPATH, "//input[@type='text' and contains(@style,'font-size')]"
)
    filename_input.clear()
    filename_input.send_keys("test1")
    numeric_inputs = driver.find_elements(
        By.XPATH, "//input[@inputmode='numeric']"
    )

    # Width
    numeric_inputs[0].click()
    numeric_inputs[0].send_keys(Keys.CONTROL, "a")
    numeric_inputs[0].send_keys("800")
    numeric_inputs[0].send_keys(Keys.TAB)

    # Height
    numeric_inputs[1].click()
    numeric_inputs[1].send_keys(Keys.CONTROL, "a")
    numeric_inputs[1].send_keys("600")
    numeric_inputs[1].send_keys(Keys.TAB)



    time.sleep(1)

    
    create_btn = driver.find_element(
    By.XPATH, "//button[normalize-space()='Create']"
)
    create_btn.click()

    print("[INFO] New canvas created")  


def draw_rectangle_with_free_pen(driver):
    print("[INFO] Selecting Free Pen via right-click...")

    wait = WebDriverWait(driver, 15)
    actions = ActionChains(driver)

    # 1. Locate Pen tool
    pen_tool = wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//button[contains(@class,'toolbtn') and contains(@title,'Pen')]")
        )
    )

    # 2. Right-click Pen tool
    actions.context_click(pen_tool).perform()
    time.sleep(0.5)

    # 3. Click "Free Pen" from context menu
    free_pen = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, "//span[normalize-space()='Free Pen']/parent::div")
        )
    )
    free_pen.click()
    time.sleep(0.5)

    print("[INFO] Free Pen selected")

    # 4. Draw rectangle using Free Pen
    canvas = wait.until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "div.pbody canvas")
        )
    )

    actions.move_to_element_with_offset(canvas, 300, 200) \
           .click_and_hold() \
           .move_by_offset(300, 0) \
           .move_by_offset(0, 200) \
           .move_by_offset(-300, 0) \
           .move_by_offset(0, -200) \
           .release() \
           .perform()

    actions.reset_actions()
    print("[INFO] Rectangle drawn using Free Pen")


    # # IMPORTANT: anchor to canvas
    # canvas = driver.find_element(By.TAG_NAME, "canvas")

    # # Move to canvas and draw
    # actions.move_to_element_with_offset(canvas, 200, 150) \
    #        .click_and_hold() \
    #        .move_by_offset(400, 300) \
    #        .release() \
    #        .perform()

    # actions.reset_actions()
    # time.sleep(1)

    # print("[INFO] Rectangle drawn successfully")



# def add_text(driver, actions):
#     print("[INFO] Adding text...")
#     actions.send_keys("t").perform()  # Text tool
#     time.sleep(1)

#     actions.move_by_offset(350, 300).click().send_keys("Automated Figure").perform()
#     time.sleep(1)

#     actions.reset_actions()


# def draw_brush(driver, actions):
#     print("[INFO] Drawing brush stroke...")
#     actions.send_keys("b").perform()  # Brush tool
#     time.sleep(1)

#     actions.move_by_offset(350, 450).click_and_hold().move_by_offset(300, 0).release().perform()
#     time.sleep(1)

#     actions.reset_actions()


# def export_image(driver, actions):
#     print("[INFO] Exporting image...")
#     actions.key_down(Keys.CONTROL).key_down(Keys.SHIFT).send_keys("e").key_up(Keys.SHIFT).key_up(Keys.CONTROL).perform()
#     time.sleep(2)

#     actions.send_keys(Keys.ENTER).perform()
#     time.sleep(3)


def main():
    options = Options()
    options.add_argument("--start-maximized")

    service = Service("chromedriver.exe")  # update path if needed
    driver = webdriver.Chrome(service=service, options=options)

    actions = ActionChains(driver)

    print("[INFO] Opening Online GIMP Editor...")
    driver.get(URL)

    wait_for_editor(driver)
    create_new_canvas(driver)

    draw_rectangle_with_free_pen(driver)
    # add_text(driver, actions)
    # draw_brush(driver, actions)
    # export_image(driver, actions)

    # print("[SUCCESS] Figure created and exported.")

    time.sleep(5)
    driver.quit()


if __name__ == "__main__":
    main()

