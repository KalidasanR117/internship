from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

import os
from dotenv import load_dotenv
load_dotenv()
ID = os.getenv('G_ID')
Pass = os.getenv('G_Pass')

service = Service(executable_path="chromedriver.exe")
driver = webdriver.Chrome(service=service)
wait = WebDriverWait(driver,15)

driver.get("https://duckduckgo.com")

wait.until(
    EC.presence_of_element_located((
        By.CLASS_NAME,"searchbox_input__rnFzM"
    ))
).send_keys("YouTube" + Keys.ENTER)

wait.until(
    EC.presence_of_element_located((
        By.XPATH,"//a[contains(@href,'https://www.youtube.com/?gl=IN')]"
    ))
).click()

search_box = wait.until(
    EC.presence_of_element_located((
        By.XPATH,"//input[contains(@name,'search_query')]"
    ))
)
search_box.send_keys("Python" + Keys.ENTER)

time.sleep(4)
driver.execute_script("window.scrollTo(0,1000);")

search_box.clear()
search_box.send_keys("java" + Keys.ENTER)

time.sleep(2)

video = wait.until(
    EC.presence_of_all_elements_located((
        By.ID,"video-title"
    ))
)

for i in range(5):
    print(video[i].text)

video[3].click()

time.sleep(4)
driver.execute_script("window.scrollTo(0,1000);")

comments = wait.until(
    EC.presence_of_all_elements_located((
        By.XPATH,"//ytd-comment-thread-renderer[contains(@class,'style-scope ytd-item-section-renderer')]"
    ))
) 
abc = []

for i in range(5):
    c = comments[i].find_element(By.ID,"content-text").text
    print(c + "\n")
    abc.append(c)

driver.get("https://duckduckgo.com")

wait.until(
    EC.presence_of_element_located((
        By.CLASS_NAME,"searchbox_input__rnFzM"
    ))
).send_keys("wu tools notepad" + Keys.ENTER)

wait.until(
    EC.presence_of_element_located((
        By.XPATH,"//a[contains(@href,'https://wutools.com/text/notepad')]"
    ))
).click()

# wait.until(
#     EC.presence_of_element_located((
#         By.XPATH,"//input[contains(@type,'email')]"
#     ))
# ).send_keys(ID)


# next = wait.until(
#     EC.presence_of_all_elements_located((
#         By.XPATH,"//button[contains(@type,'button')]"
#     ))
# )
# next[2].click()
#
time.sleep(3)
wait.until(
    EC.presence_of_element_located((
        By.XPATH,"//textarea[contains(@id,'notepadText')]"
    ))
).send_keys(abc)
# mce-content-body 
time.sleep(100)

