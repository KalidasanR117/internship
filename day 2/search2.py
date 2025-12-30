from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
import time
from ollama import Client

service = Service(executable_path="chromedriver.exe")
driver = webdriver.Chrome(service=service)
wait = WebDriverWait(driver, 15)


def get_article_links(base_url, limit=10):
    driver.get(base_url)
    time.sleep(3)

    anchors = driver.find_elements(By.TAG_NAME, "a")
    links = []

    for a in anchors:
        href = a.get_attribute("href")

        if not href:
            continue

     
        if href.startswith("http") and \
           not any(x in href for x in ["login", "signup", "account", "#"]):
            links.append(href)

        if len(links) >= limit:
            break

    return list(dict.fromkeys(links))  

def extract_article(url):
    driver.get(url)
    time.sleep(2)


    try:
        title = wait.until(
            EC.presence_of_element_located((By.TAG_NAME, "h1"))
        ).text.strip()
    except:
        title = "No title found"


    paragraphs = driver.find_elements(By.TAG_NAME, "p")
    content = []

    for p in paragraphs:
        text = p.text.strip()
        if len(text) > 40:   
            content.append(text)

    if not content:
        return None

    article_text = title + "\n\n" + "\n".join(content)
    return article_text


client = Client()

def summarize_text(text):
    prompt = f"""
    Summarize the following news article in 5-7 bullet points.
    Focus on key facts and avoid repetition.

    ARTICLE:
    {text}
    """

    response = client.chat(
        model="llama2",
        messages=[{"role": "user", "content": prompt}]
    )

    return response["message"]["content"]



BASE_URL = "https://en.wikipedia.org/wiki/Donald_Trump"  

print("Collecting links...")
urls = get_article_links(BASE_URL)

print(f"Found {len(urls)} links\n")

articles = []

for url in urls:
    print(f"Extracting: {url}")
    article = extract_article(url)
    if article:
        articles.append(article)
    time.sleep(2)

print("\nRunning Ollama summaries...\n")

for idx, article in enumerate(articles, start=1):
    summary = summarize_text(article)
    print(f"SUMMARY {idx}")
    print(summary)
    print("-" * 60)

driver.quit()
