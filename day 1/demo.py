from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time



Service = Service(executable_path="chromedriver.exe")
driver = webdriver.Chrome(service=Service)
wait = WebDriverWait(driver,25)

driver.get("https://google.com")
search = wait.until(
    EC.presence_of_element_located((By.CLASS_NAME,"gLFyf"))
)
search.send_keys("YouTube")
search.send_keys(Keys.ENTER)


wait.until(
    EC.presence_of_element_located(
        (By.PARTIAL_LINK_TEXT, "YouTube")
    )
).click()

search_youtube = wait.until(
    EC.presence_of_element_located((By.NAME,"search_query"))
)

search_youtube.send_keys("Python" + Keys.ENTER)
time.sleep(3)
search_youtube.clear()
search_youtube.send_keys("Java" + Keys.ENTER)


video = wait.until(
    EC.presence_of_all_elements_located(
        (By.ID,"video-title")
    )
)

for i in range(5):
    print(video[i].text)

video[3].click()
time.sleep(5)
driver.execute_script("window.scrollTo(0,1000);")

comment = wait.until(

    EC.presence_of_all_elements_located((By.XPATH,"//ytd-comment-thread-renderer[contains(@class,'style-scope ytd-item-section-renderer')]"))

    )   

for i in range(5):
    print(comment[i].find_element(By.ID,"content-text").text)
    print("\n")



time.sleep(50)
driver.quit()   