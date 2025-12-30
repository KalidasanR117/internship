from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time



service = Service(executable_path="chromedriver.exe")
driver = webdriver.Chrome(service=service)
wait = WebDriverWait(driver,5)
driver.get("https://youtube.com")

input_element = driver.find_element(By.NAME,"search_query")
input_element.send_keys("Python")
input_element.send_keys(Keys.ENTER)


wait.until(
    EC.presence_of_all_elements_located((By.ID,"video-title"))

)

video = driver.find_elements(By.ID,"video-title")

for i in range(5):
    print(video[i].text)

video[4].click()
time.sleep(3)
driver.execute_script("window.scrollTo(0,1000);")
comments = wait.until(
    EC.presence_of_all_elements_located((By.XPATH,"//ytd-comment-thread-renderer[contains(@class,'style-scope ytd-item-section-renderer')]"))
)
for i in range(5):
    print(comments[i].find_element(By.ID,"content-text").text)
# wait
# # signin = driver.find_element(By.CLASS_NAME,"style-scope ytd-masthead").click()
# signin = driver.find_element(By.XPATH,'//a[contains(@aria-label,"Sign in")]')
# signin.click()

# sign_in_bar = wait.until(
#     EC.presence_of_element_located((By.XPATH,'//input[contains(@type,"email")]'))
# )
# sign_in_bar.send_keys("qfs")

# driver.find_element(By.XPATH,'//div[contains(@data-primary-action-label,"Next")]').click()



# like = wait.until(
#     EC.element_to_be_clickable((By.XPATH,'//button[contains(@aria-label,"like this")]'))
# )

# like.click()
# input_element.clear()
# input_element.send_keys("java")
# input_element.send_keys(Keys.ENTER)

time.sleep(100)

driver.quit()

