from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
import time

service = Service(executable_path="chromedriver.exe")
driver = webdriver.Chrome(service=service)

driver.get("https://bbc.com/news")

wait = WebDriverWait(driver,15)

links = wait.until(
    EC.presence_of_all_elements_located(
        (By.XPATH,"//a[contains(@href,'/news/') and not(contains(@href,'/news/live/')) and not(@data-testid='subNavigationLink')]")

    )
)

link = []
for a in links:
    href = a.get_attribute("href")
    if href and href not in links:
        link.append(href)

articles = []
for a in link[:5]:
    driver.get(a)
    heading = wait.until(
        EC.presence_of_element_located(
            (By.XPATH,"//div[@data-component='headline-block']//h1")
        )
    )
    all_text = wait.until(
        EC.presence_of_all_elements_located(
            (By.XPATH,"//div[@data-component='text-block']//p")
        )
    ) 
    full = heading.text + "\n"
    for i in all_text:
        full = full +  i.text + "\n"
    articles.append(full)
    time.sleep(2)
# print(articles)

from ollama import Client

client = Client()

def summarize_text(text):
    prompt = f"Summarize the following article text:\n\n{text}"
    
    response = client.chat(
        model="llama2", 
        messages=[{"role": "user", "content": prompt}]
    )
    
    
    return response["message"]["content"]

print("ollama...")
summaries = []
for article in articles[1:]:
    summary = summarize_text(article)
    print("Summary:")
    print(summary)
    print("-------------")
    summaries.append(summary)


time.sleep(10)